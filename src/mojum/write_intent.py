"""Durable local write-ahead intent for synthetic Aquarium mutations.

The registry freezes *cooperating* writers BEFORE the Noosphere commit.
A committed write can then be matched to a predeclared exact event hash.
This is not a distributed lease or proof that a privileged raw writer did
not forge the same event. No automatic restart/repair is provided.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import quote

from .aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS
from .genesis import GenomeLedger
from .guarded_aquarium import GuardedAquarium, _existing_regular_file
from .local_authority import AuthorityError, LocalAuthority, _identity, _int, _writer, _MAX_INTEGER
from .noosphere import _sha

SCHEMA = "mojum.local-write-intent/1"


def _require_full_wal(db, name):
    """Check the LIVE SQLite connection, not a new reader's PRAGMA defaults.

    journal_mode is persistent in the DB, but synchronous is per connection.
    A separate read-only auditor cannot establish the writer's synchronous
    setting. Every opt-in prepared mutation checks both owner connections.
    """
    try:
        mode = db.execute("PRAGMA journal_mode").fetchone()
        sync = db.execute("PRAGMA synchronous").fetchone()
    except sqlite3.Error as exc:
        raise AuthorityError("durable SQLite settings unavailable for " + name) from exc
    if (mode is None or str(mode[0]).lower() != "wal"
            or sync is None or type(sync[0]) is not int or sync[0] != 2):
        raise AuthorityError(
            name + " requires live SQLite journal_mode=WAL and synchronous=FULL"
        )


def _installed(db):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='write_intents'"
    ).fetchone() is not None


def install_intent_table(registry_path):
    """Explicit local schema opt-in; never create or replace a registry file."""
    with LocalAuthority(registry_path) as store:
        with store._write():
            store._db.execute(
                """CREATE TABLE IF NOT EXISTS write_intents(
                    organism_id TEXT PRIMARY KEY NOT NULL,
                    writer TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    previous_head TEXT NOT NULL,
                    previous_count INTEGER NOT NULL,
                    previous_tick INTEGER NOT NULL,
                    expected_head TEXT NOT NULL,
                    expected_tick INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('step','acquire'))
                )"""
            )


def _pending(store, organism_id):
    if not _installed(store._db):
        raise AuthorityError("durable write-intent table not installed")
    x = store._db.execute(
        "SELECT * FROM write_intents WHERE organism_id=?",
        (organism_id,),
    ).fetchone()
    return dict(x) if x is not None else None


class IntentGuardedAquarium(GuardedAquarium):
    """Opt-in synchronous writer; prepare registry freeze, append, finalize.

    One registry FROZEN state has pending_writer=NULL and a matching intent
    record. Transfer activation requires a named target and cannot authorize
    it while the intent is pending. Direct raw journal writes remain possible
    for clients with service-UID filesystem access (outside trust boundary).
    """

    def __init__(self, db_path, registry_path, seed, graph, *,
                 writer, epoch, generation, **kwargs):
        super().__init__(
            db_path, registry_path, seed, graph,
            writer=writer, epoch=epoch, generation=generation, **kwargs,
        )
        try:
            self._check_durability()
            if not _installed(self._registry._db):
                raise AuthorityError("write-intent schema must be explicitly installed")
            if _pending(self._registry, self._organism_id) is not None:
                raise AuthorityError("unreconciled prepared write; fail closed")
        except BaseException:
            self.close()
            raise

    def _check_durability(self):
        _require_full_wal(self._registry._db, "authority registry")
        _require_full_wal(self._aq.store._db, "Noosphere journal")

    def _plan(self, kind, arg):
        if kind == "step":
            dx, dy = arg
            payload, _, _ = self._aq._calculate(dx, dy)
            return self._aq._record(
                "aquarium.frame", f"frame-{self._aq.tick:012d}", payload,
                self._aq.tick * self._aq.dt_ns, self._aq.genome.revision,
            )
        if kind == "acquire":
            if type(arg) is not dict:
                raise AquariumError("acquisition must be a JSON event object")
            if arg.get("valid_from_tick", -1) < self._aq.tick:
                raise AquariumError("cannot retroactively change committed frames")
            if arg.get("recorded_at_ns") != self._aq.tick * self._aq.dt_ns:
                raise AquariumError("acquisition and simulation clock mismatch")
            proposed = GenomeLedger.from_snapshot(self._aq.genome.snapshot())
            proposed.append(arg, proposed.revision)
            return self._aq._record(
                "genesis.acquisition", "acq-" + arg["event_id"],
                {"acquisition": arg}, self._aq.tick * self._aq.dt_ns,
                proposed.revision,
            )
        raise AuthorityError("unknown prepared mutation kind")

    def _prepare(self, kind, arg):
        self._live()
        self._check_durability()
        with self._registry._write():
            row = self._registry._row(self._organism_id)
            self._check_admission(row)
            self._check_tip(row)
            if _pending(self._registry, self._organism_id) is not None:
                raise AuthorityError("existing prepared intent forbids new write")
            event = self._plan(kind, arg)
            expected_head = _sha({"previous": row["head"], "event": event})
            expected_tick = row["tick"] + (1 if kind == "step" else 0)
            self._registry._db.execute(
                "INSERT INTO write_intents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self._organism_id, self._writer, self._epoch, self._generation,
                 row["head"], row["event_count"], row["tick"],
                 expected_head, expected_tick, event["event_id"], kind),
            )
            cursor = self._registry._db.execute(
                "UPDATE authority SET phase='FROZEN' "
                "WHERE organism_id=? AND phase='ACTIVE' AND writer=? "
                "AND epoch=? AND generation=? AND event_count=? AND head=? AND tick=? "
                "AND pending_writer IS NULL",
                (self._organism_id, self._writer, self._epoch, self._generation,
                 row["event_count"], row["head"], row["tick"]),
            )
            if cursor.rowcount != 1:
                raise AuthorityError("source admission was lost during durable prepare")
        # Only now (AFTER the intent transaction committed) may journal append.
        self._poisoned = True
        return expected_head, expected_tick

    def _finish(self, expected_head, expected_tick):
        # If a live SQLite connection was degraded after an append, keep
        # the durable intent FROZEN instead of finalizing a weak commit.
        self._check_durability()
        with self._registry._write():
            self._check_path_identity()
            row = self._registry._row(self._organism_id)
            intent = _pending(self._registry, self._organism_id)
            if (row["phase"] != "FROZEN"
                    or row["pending_writer"] is not None
                    or row["writer"] != self._writer
                    or row["epoch"] != self._epoch
                    or row["generation"] != self._generation
                    or intent is None
                    or intent["expected_head"] != expected_head
                    or intent["expected_tick"] != expected_tick
                    or intent["previous_head"] != row["head"]
                    or intent["previous_count"] != row["event_count"]
                    or intent["previous_tick"] != row["tick"]
                    or intent["writer"] != self._writer
                    or intent["epoch"] != self._epoch
                    or intent["generation"] != self._generation
                    or self._generation >= _MAX_INTEGER):
                raise AuthorityError("prepared intent or local writer changed")
            if (self._aq.store.head() != expected_head
                    or self._aq._known_head != expected_head
                    or self._aq.store.count() != intent["previous_count"] + 1
                    or self._aq.tick != expected_tick):
                raise AuthorityError("committed journal does not match prepared event")
            cursor = self._registry._db.execute(
                "UPDATE authority SET phase='ACTIVE', head=?, tick=?, "
                "event_count=?, generation=? WHERE organism_id=? "
                "AND phase='FROZEN' AND writer=? AND epoch=? AND generation=? "
                "AND head=? AND event_count=? AND tick=? AND pending_writer IS NULL",
                (expected_head, expected_tick, intent["previous_count"] + 1,
                 self._generation + 1, self._organism_id, self._writer, self._epoch,
                 self._generation, intent["previous_head"],
                 intent["previous_count"], intent["previous_tick"]),
            )
            if cursor.rowcount != 1:
                raise AuthorityError("prepared commit lost authority CAS")
            if self._registry._db.execute(
                "DELETE FROM write_intents WHERE organism_id=? AND expected_head=?",
                (self._organism_id, expected_head),
            ).rowcount != 1:
                raise AuthorityError("prepared intent vanished")
        self._generation += 1
        self._poisoned = False

    def _prepared_mutation(self, kind, value):
        # All input is validated and its exact expected event hash computed
        # while source authority is ACTIVE. Only then is FROZEN committed.
        intended = self._prepare(kind, value)
        # If there is any failure after durable prepare, leave the registry
        # frozen and do not auto-retry or silently remove prepared evidence.
        if kind == "step":
            result = self._aq.step(dx=value[0], dy=value[1])
        else:
            result = self._aq.acquire(value)
        self._finish(*intended)
        return result

    def step(self, *, dx=0.02, dy=0.0):
        return self._prepared_mutation("step", (dx, dy))

    def acquire(self, acquired):
        return self._prepared_mutation("acquire", copy.deepcopy(acquired))


def inspect_prepared(journal_path, registry_path, seed, graph, *,
                     organism_id, aquarium_options=None):
    """Read-only verified inspection of durable PREPARED state.

    A matched event only describes exact committed content vs intent; it
    never proves that the process which wrote it was authorized.
    """
    _identity(organism_id)
    if aquarium_options is None:
        aquarium_options = {}
    if type(aquarium_options) is not dict or any(
        k in aquarium_options
        for k in ("agent_id", "db_path", "seed", "graph", "external_expected_head")
    ):
        raise AuthorityError("invalid Aquarium replay configuration")
    journal, inode = _existing_regular_file(journal_path, "Aquarium journal")
    with LocalAuthority(registry_path) as store:
        with store._write():
            row = store._row(organism_id)
            intent = _pending(store, organism_id)
            if intent is None:
                if row["phase"] != "ACTIVE" or row["pending_writer"] is not None:
                    return _status(
                        row, "AUTHORITY_FROZEN_WITHOUT_INTENT_STOP", None, None, None,
                    )
                # A missing intent is NOT evidence that the current journal
                # matches the registry. Verify the snapshot below even then.
            elif (row["phase"] != "FROZEN" or row["pending_writer"] is not None
                  or row["writer"] != intent["writer"]
                  or row["epoch"] != intent["epoch"]
                  or row["generation"] != intent["generation"]
                  or row["head"] != intent["previous_head"]
                  or row["event_count"] != intent["previous_count"]
                  or row["tick"] != intent["previous_tick"]):
                return _status(row, "UNTRUSTED_INTENT_STATE_STOP", intent, None, None)

            # SQLite Backup API includes WAL commits, unlike copying the
            # primary *.sqlite3 file. Stage data in an owner-private folder.
            uri = "file:" + quote(os.path.abspath(journal), safe="/") + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as source:
                source.execute("PRAGMA query_only=ON")
                with tempfile.TemporaryDirectory(prefix=".mojum-intent-", dir=journal.parent) as scratch:
                    stage = Path(scratch) / "journal.sqlite3"
                    with closing(sqlite3.connect(stage)) as target:
                        source.backup(target, pages=256, sleep=0.01)
                    try:
                        with Aquarium(stage, seed, graph, agent_id=organism_id,
                                      **aquarium_options) as replay:
                            count = replay.store.count()
                            head = replay.store.verify_chain()["head"]
                            tick = replay.tick
                            if not 1 <= count <= MAX_REPLAY_EVENTS:
                                raise AquariumError("invalid journal count")
                    except (ValueError, OverflowError, RecursionError, sqlite3.Error):
                        return _status(row, "JOURNAL_INVALID_STOP", intent, None, None)
                if _existing_regular_file(journal, "Aquarium journal")[1] != inode:
                    return _status(row, "JOURNAL_REPLACED_STOP", intent, None, None)
                source.execute("BEGIN")
                try:
                    actual_count = source.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                    final = source.execute(
                        "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
                    ).fetchone()
                    actual_head = final[0] if final else "0" * 64
                finally:
                    source.execute("ROLLBACK")
                if (actual_count, actual_head) != (count, head):
                    return _status(row, "JOURNAL_CHANGED_DURING_AUDIT_STOP",
                                   intent, count, head)

            if intent is None:
                if (count == row["event_count"] and head == row["head"]
                        and tick == row["tick"]):
                    label = "NO_PREPARED_INTENT"
                else:
                    label = "UNANCHORED_JOURNAL_WITHOUT_INTENT_STOP"
                return _status(row, label, None, count, head)

            if (count == intent["previous_count"]
                    and head == intent["previous_head"]
                    and tick == intent["previous_tick"]):
                label = "PREPARED_NO_EVENT_STOP"
            elif (count == intent["previous_count"] + 1
                  and head == intent["expected_head"]
                  and tick == intent["expected_tick"]):
                label = "PREPARED_EXACT_EVENT_REQUIRES_REVIEW"
            else:
                label = "PREPARED_MISMATCH_STOP"
            return _status(row, label, intent, count, head)


def _status(row, classification, intent, event_count, journal_head):
    return {
        "schema": SCHEMA,
        "organism_id": row["organism_id"],
        "classification": classification,
        "authority": {
            "phase": row["phase"], "epoch": row["epoch"],
            "generation": row["generation"], "head": row["head"],
            "event_count": row["event_count"], "tick": row["tick"],
        },
        "intent": ({"kind": intent["kind"],
                    "expected_head": intent["expected_head"],
                    "expected_tick": intent["expected_tick"]} if intent else None),
        "journal": {"event_count": event_count, "head": journal_head},
        "auto_repair_allowed": False,
        "automatic_writer_promotion_allowed": False,
        "operator_review_required": classification != "NO_PREPARED_INTENT",
        "trust": "local_verified_content_not_authenticated_writer_identity",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect durable write intent; no automatic repair/authority grants",
    )
    mode = parser.add_subparsers(dest="command", required=True)
    install = mode.add_parser("install")
    install.add_argument("--registry", required=True)
    inspect = mode.add_parser("inspect")
    inspect.add_argument("--registry", required=True)
    inspect.add_argument("--journal", required=True)
    inspect.add_argument("--organism-id", required=True)
    inspect.add_argument("--genome", type=Path, required=True)
    inspect.add_argument("--graph", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install_intent_table(args.registry)
            return 0
        result = inspect_prepared(
            args.journal, args.registry,
            json.loads(args.genome.read_text(encoding="utf-8")),
            json.loads(args.graph.read_text(encoding="utf-8")),
            organism_id=args.organism_id,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["classification"] == "NO_PREPARED_INTENT" else 3
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
