"""Single-host, fail-closed authority handoff journal for synthetic Mojum.

A local trusted SQLite coordinator serializes cooperative API admissions and
handoffs. NOT a distributed consensus, cryptographic credential, or a fence
against clients that bypass this registry or copy it to disconnected hosts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .journal_lineage import JournalEvidence, compare_evidence, inspect_archive
from .mesh_contract import _NODE_ID

_SCHEMA = "mojum.local-authority/1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_MAX_INTEGER = 2**63 - 1


class AuthorityError(ValueError):
    """Missing, stale, split, unauthorized or inconsistent local handoff."""


def _identity(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise AuthorityError("invalid organism identity")
    return value


def _writer(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or _NODE_ID.fullmatch(value) is None):
        raise AuthorityError("invalid canonical writer node id")
    return value


def _int(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        raise AuthorityError("invalid " + name)
    return value


def _evidence(value):
    if not isinstance(value, JournalEvidence):
        raise AuthorityError("validated journal evidence required")
    _identity(value.organism_id)
    _int(value.tick, "tick")
    if (type(value.head) is not str or _SHA.fullmatch(value.head) is None
            or type(value.manifest_sha256) is not str
            or _SHA.fullmatch(value.manifest_sha256) is None
            or type(value.event_hashes) is not tuple
            or not 1 <= len(value.event_hashes) <= 10_000
            or any(type(x) is not str or _SHA.fullmatch(x) is None
                   for x in value.event_hashes)
            or value.event_hashes[-1] != value.head):
        raise AuthorityError("invalid journal evidence")
    return value


class LocalAuthority:
    """One trusted local registry; NEVER independently clone to two writers.

    The registry does not append Aquarium events; all cooperating processes
    must consult the *same* trusted registry before writes, and separately
    verify the state after journal commits. An unchecked direct Aquarium.step()
    can bypass this protocol, so this is an admission prototype only.
    """

    def __init__(self, path, *, create=False):
        path = Path(path)
        if (not path.name or not path.parent.is_dir()
                or path.parent.is_symlink() or path.is_symlink()):
            raise AuthorityError("registry requires trusted existing parent and regular file")
        if create:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags, 0o600)
            os.close(fd)
        else:
            if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
                raise AuthorityError("local authority registry is missing")
        # mode=rw makes an unavailable registry fail closed rather than
        # silently creating a replacement (which could regrant old writers).
        self._db = sqlite3.connect(
            "file:" + _url_quote(os.path.abspath(path)) + "?mode=rw",
            uri=True, isolation_level=None, timeout=1.0,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=1000")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA trusted_schema=OFF")
        if create:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """CREATE TABLE authority (
                    organism_id TEXT PRIMARY KEY NOT NULL,
                    phase TEXT NOT NULL CHECK(phase IN ('ACTIVE','FROZEN')),
                    writer TEXT NOT NULL,
                    pending_writer TEXT,
                    epoch INTEGER NOT NULL,
                    event_count INTEGER NOT NULL,
                    tick INTEGER NOT NULL,
                    head TEXT NOT NULL,
                    generation INTEGER NOT NULL
                )"""
            )
        elif self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='authority'"
        ).fetchone() is None:
            self._db.close()
            raise AuthorityError("not an initialized local authority registry")

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _write(self):
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise AuthorityError("local authority registry unavailable or locked") from exc
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _row(self, organism_id):
        _identity(organism_id)
        value = self._db.execute(
            "SELECT * FROM authority WHERE organism_id=?", (organism_id,),
        ).fetchone()
        if value is None:
            raise AuthorityError("organism authority not provisioned")
        return dict(value)

    def state(self, organism_id):
        """Metadata only, no journal payloads. State is not an auth token."""
        return {"schema": _SCHEMA, **self._row(organism_id)}

    def provision(self, evidence, writer):
        """Explicit first enrollment; caller must validate trusted source first."""
        evidence = _evidence(evidence)
        _writer(writer)
        with self._write():
            if self._db.execute(
                "SELECT 1 FROM authority WHERE organism_id=?",
                (evidence.organism_id,),
            ).fetchone() is not None:
                raise AuthorityError("organism already provisioned")
            self._db.execute(
                "INSERT INTO authority VALUES(?, 'ACTIVE', ?, NULL, 1, ?, ?, ?, 1)",
                (evidence.organism_id, writer, len(evidence.event_hashes),
                 evidence.tick, evidence.head),
            )
        return self.state(evidence.organism_id)

    def _exact(self, row, evidence):
        evidence = _evidence(evidence)
        if (row["organism_id"] != evidence.organism_id
                or row["head"] != evidence.head
                or row["event_count"] != len(evidence.event_hashes)
                or row["tick"] != evidence.tick):
            raise AuthorityError("journal head/tick differs from authority anchor")
        return evidence

    def admit(self, evidence, *, writer, epoch, generation):
        """Read-only admission; caller MUST revalidate against same registry.

        Admission is not a durable network lease or a capability token, and
        cannot atomically authorize writes in a separate SQLite database.
        """
        _writer(writer)
        _int(epoch, "epoch", 1)
        _int(generation, "generation", 1)
        with self._write():
            row = self._row(_evidence(evidence).organism_id)
            if (row["phase"] != "ACTIVE" or row["writer"] != writer
                    or row["epoch"] != epoch
                    or row["generation"] != generation):
                raise AuthorityError("writer is not currently admitted")
            self._exact(row, evidence)
        return {"status": "LOCAL_ADMISSION_ONLY_NOT_DISTRIBUTED_AUTHORITY",
                "organism_id": evidence.organism_id,
                "writer": writer, "epoch": epoch, "generation": generation,
                "head": evidence.head}

    def progress(self, evidence, *, writer, epoch, generation):
        """Advance the anchored journal after independently committed events.

        Requiring the stored tip to remain a strict prefix detects forks,
        skips/replays, stale copies and regressions. A caller-supplied hash
        chain is not proof of trusted device identity.
        """
        evidence = _evidence(evidence)
        _writer(writer)
        _int(epoch, "epoch", 1)
        _int(generation, "generation", 1)
        with self._write():
            row = self._row(evidence.organism_id)
            if (row["phase"] != "ACTIVE" or row["writer"] != writer
                    or row["epoch"] != epoch
                    or row["generation"] != generation):
                raise AuthorityError("stale or unadmitted journal writer")
            old_count = row["event_count"]
            if (len(evidence.event_hashes) <= old_count
                    or evidence.event_hashes[old_count - 1] != row["head"]
                    or evidence.tick < row["tick"]
                    or row["generation"] >= _MAX_INTEGER):
                raise AuthorityError("journal does not extend the trusted prefix")
            self._db.execute(
                "UPDATE authority SET event_count=?, tick=?, head=?, generation=? "
                "WHERE organism_id=?",
                (len(evidence.event_hashes), evidence.tick, evidence.head,
                 row["generation"] + 1, evidence.organism_id),
            )
        return self.state(evidence.organism_id)

    def freeze_transfer(self, source, target, *, source_writer, target_writer,
                        epoch, generation):
        """Revoke LOCAL admissions first; require exact matching histories.

        Both source/target evidence must come from separately verified cold
        archives, never from merely copying claimed names or epoch numbers.
        """
        source, target = _evidence(source), _evidence(target)
        _writer(source_writer)
        _writer(target_writer)
        _int(epoch, "epoch", 1)
        _int(generation, "generation", 1)
        if source_writer == target_writer:
            raise AuthorityError("handoff requires a distinct target writer")
        lineage = compare_evidence(source, target)
        if lineage["classification"] != "IDENTICAL_HISTORY":
            raise AuthorityError("cannot hand off diverged or unequal histories")
        with self._write():
            row = self._row(source.organism_id)
            if (row["phase"] != "ACTIVE" or row["writer"] != source_writer
                    or row["epoch"] != epoch or row["generation"] != generation
                    or row["generation"] >= _MAX_INTEGER):
                raise AuthorityError("source lacks current local admission")
            self._exact(row, source)
            self._db.execute(
                "UPDATE authority SET phase='FROZEN', pending_writer=?, generation=? "
                "WHERE organism_id=?",
                (target_writer, generation + 1, source.organism_id),
            )
        return self.state(source.organism_id)

    def activate_transfer(self, evidence, *, target_writer,
                          prior_epoch, frozen_generation):
        """Activate exactly the staged target from the same trusted registry.

        There is intentionally NO automatic timeout/rollback. If a device
        disappears mid-handoff, the registry remains FROZEN until an
        explicitly reviewed recovery protocol exists.
        """
        evidence = _evidence(evidence)
        _writer(target_writer)
        _int(prior_epoch, "prior epoch", 1)
        _int(frozen_generation, "frozen generation", 1)
        with self._write():
            row = self._row(evidence.organism_id)
            if (row["phase"] != "FROZEN"
                    or row["pending_writer"] != target_writer
                    or row["epoch"] != prior_epoch
                    or row["generation"] != frozen_generation
                    or row["epoch"] >= _MAX_INTEGER
                    or row["generation"] >= _MAX_INTEGER):
                raise AuthorityError("target is not the pending frozen handoff")
            self._exact(row, evidence)
            self._db.execute(
                "UPDATE authority SET phase='ACTIVE', pending_writer=NULL, "
                "writer=?, epoch=?, generation=? WHERE organism_id=?",
                (target_writer, prior_epoch + 1, frozen_generation + 1,
                 evidence.organism_id),
            )
        return self.state(evidence.organism_id)


def _url_quote(path):
    from urllib.parse import quote
    return quote(path, safe="/")


def main(argv=None):
    """Single-host audit CLI. No private event payloads in stdout."""
    parser = argparse.ArgumentParser(
        description="Local-only Mojum authority admission; NO distributed leadership",
    )
    modes = parser.add_subparsers(dest="command", required=True)
    for mode in ("provision", "status", "admit", "progress", "freeze", "activate"):
        item = modes.add_parser(mode)
        item.add_argument("--registry", required=True)
        item.add_argument("--organism-id", required=True)
        if mode != "status":
            item.add_argument("--archive", required=True)
            item.add_argument("--tick", type=int, required=True)
            item.add_argument("--manifest-sha256")
        if mode in ("provision", "admit", "progress"):
            item.add_argument("--writer", required=True)
        if mode in ("admit", "progress", "freeze"):
            item.add_argument("--epoch", type=int, required=True)
            item.add_argument("--generation", type=int, required=True)
        if mode == "freeze":
            item.add_argument("--source-writer", required=True)
            item.add_argument("--target-writer", required=True)
            item.add_argument("--target-archive", required=True)
            item.add_argument("--target-manifest-sha256")
        if mode == "activate":
            item.add_argument("--target-writer", required=True)
            item.add_argument("--prior-epoch", type=int, required=True)
            item.add_argument("--frozen-generation", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = None
        if args.command != "status":
            evidence = inspect_archive(
                args.archive, expected_organism_id=args.organism_id,
                expected_tick=args.tick, expected_manifest_sha256=args.manifest_sha256,
            )
        with LocalAuthority(args.registry, create=args.command == "provision") as store:
            if args.command == "provision":
                result = store.provision(evidence, args.writer)
            elif args.command == "status":
                result = store.state(args.organism_id)
            elif args.command == "admit":
                result = store.admit(evidence, writer=args.writer,
                                     epoch=args.epoch, generation=args.generation)
            elif args.command == "progress":
                result = store.progress(evidence, writer=args.writer,
                                       epoch=args.epoch, generation=args.generation)
            elif args.command == "freeze":
                target = inspect_archive(
                    args.target_archive, expected_organism_id=args.organism_id,
                    expected_tick=args.tick,
                    expected_manifest_sha256=args.target_manifest_sha256,
                )
                result = store.freeze_transfer(
                    evidence, target, source_writer=args.source_writer,
                    target_writer=args.target_writer,
                    epoch=args.epoch, generation=args.generation,
                )
            else:
                result = store.activate_transfer(
                    evidence, target_writer=args.target_writer,
                    prior_epoch=args.prior_epoch,
                    frozen_generation=args.frozen_generation,
                )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
