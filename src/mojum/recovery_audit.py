"""Fail-closed, snapshot-based diagnosis of Aquarium vs authority crash gaps.

This is FORENSICS ONLY. No registry head/epoch/generation updates, no event
replay to the live source, no auto-accept of a 'one extra event' state.

A locally trusted registry serializes cooperating writers while a read-only
SQLite backup of the source journal is made and replayed in a private scratch
directory. Direct writers that bypass this registry may still race.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import quote

from .aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS
from .genesis import GenesisError
from .guarded_aquarium import _existing_regular_file
from .local_authority import AuthorityError, LocalAuthority, _identity, _int, _writer
from .noosphere import NoosphereError

SCHEMA = "mojum.recovery.offline-audit/1"


def _report(row, *, classification, count=None, tick=None, head=None,
            common_anchor=None):
    """Metadata only; PRIVATE event/payload/proprioception bytes are excluded."""
    return {
        "schema": SCHEMA,
        "organism_id": row["organism_id"],
        "classification": classification,
        "registry": {
            "phase": row["phase"],
            "writer": row["writer"],
            "epoch": row["epoch"],
            "generation": row["generation"],
            "event_count": row["event_count"],
            "tick": row["tick"],
            "head": row["head"],
        },
        "journal": {"event_count": count, "tick": tick, "head": head},
        "common_registry_anchor": common_anchor,
        "auto_repair_allowed": False,
        "operator_review_required": classification != "IN_SYNC",
        "trust": "local_snapshot_not_authenticated_host_or_distributed_authority",
    }


def _live_status(source):
    """Two SQL reads in one snapshot; never read stale per-statement heads."""
    source.execute("BEGIN")
    try:
        row = source.execute(
            "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        count = source.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return count, row[0] if row else "0" * 64
    finally:
        source.execute("ROLLBACK")


def inspect_recovery(
    journal_path, registry_path, seed, graph, *, organism_id, writer,
    expected_epoch, expected_generation, aquarium_options=None,
):
    """Classify two durable stores at one cooperative local pause point.

    This function NEVER changes the registry anchor and NEVER authorizes a
    takeover, even when exactly one journal event follows the anchored head.
    The caller must supply the actual seed/graph/clock configuration to
    prove complete deterministic replay on a copied journal.
    """
    _identity(organism_id)
    _writer(writer)
    _int(expected_epoch, "epoch", 1)
    _int(expected_generation, "generation", 1)
    if aquarium_options is None:
        aquarium_options = {}
    if type(aquarium_options) is not dict:
        raise AuthorityError("aquarium_options must be explicit dictionary")
    forbidden = {"agent_id", "external_expected_head", "db_path", "seed", "graph"}
    if any(k in aquarium_options for k in forbidden):
        raise AuthorityError("cannot override pinned organism or journal identity")
    journal, journal_inode = _existing_regular_file(journal_path, "Aquarium journal")
    registry, registry_inode = _existing_regular_file(registry_path, "authority registry")

    # Open only an existing read-only source journal. This must not create
    # missing files or bootstrap another Aquarium while diagnosing a crash.
    read_uri = "file:" + quote(os.path.abspath(journal), safe="/") + "?mode=ro"
    with LocalAuthority(registry) as authority:
        with authority._write():
            row = authority._row(organism_id)
            if (row["writer"] != writer or row["epoch"] != expected_epoch
                    or row["generation"] != expected_generation):
                raise AuthorityError("authority changed; expected writer epoch/generation mismatch")
            if _existing_regular_file(registry, "authority registry")[1] != registry_inode:
                raise AuthorityError("authority registry replaced during audit")
            if _existing_regular_file(journal, "Aquarium journal")[1] != journal_inode:
                raise AuthorityError("journal path replaced before audit")

            with closing(sqlite3.connect(read_uri, uri=True, timeout=1.0)) as source:
                source.execute("PRAGMA query_only=ON")
                # Staging is owner-private and automatically removed. Unlike a
                # raw file copy, sqlite3.backup includes committed WAL changes.
                with tempfile.TemporaryDirectory(
                    prefix=".mojum-audit-", dir=journal.parent,
                ) as scratch:
                    stage = Path(scratch) / "journal.sqlite3"
                    with closing(sqlite3.connect(stage)) as target:
                        source.backup(target, pages=256, sleep=0.01)
                    try:
                        with Aquarium(
                            stage, seed, graph, agent_id=organism_id,
                            **aquarium_options,
                        ) as shadow:
                            checkpoint = shadow.checkpoint()
                            chain = shadow.store.verify_chain(
                                external_expected_head=checkpoint["journal_head"],
                            )
                            count = chain["count"]
                            head = chain["head"]
                            tick = checkpoint["tick"]
                            if not 1 <= count <= MAX_REPLAY_EVENTS:
                                raise AquariumError("audit event count exceeds bound")
                            anchor_row = shadow.store._db.execute(
                                "SELECT event_hash FROM events WHERE seq=?",
                                (row["event_count"],),
                            ).fetchone()
                            ancestor = anchor_row[0] if anchor_row else None
                    except (ValueError, TypeError, RecursionError, sqlite3.Error,
                            OverflowError) as exc:
                        # Never return the exception string: it may include
                        # private event content from validation failures.
                        return _report(row, classification="JOURNAL_INVALID_STOP")

                try:
                    latest_count, latest_head = _live_status(source)
                except sqlite3.Error:
                    return _report(row, classification="JOURNAL_INVALID_STOP")
                if _existing_regular_file(journal, "Aquarium journal")[1] != journal_inode:
                    return _report(row, classification="SOURCE_REPLACED_STOP")
                if _existing_regular_file(registry, "authority registry")[1] != registry_inode:
                    return _report(row, classification="REGISTRY_REPLACED_STOP")

                if (count, head) != (latest_count, latest_head):
                    status = "JOURNAL_CHANGED_DURING_AUDIT_STOP"
                elif row["phase"] != "ACTIVE":
                    status = "AUTHORITY_FROZEN_STOP"
                elif (count == row["event_count"]
                      and head == row["head"]
                      and tick == row["tick"]):
                    status = "IN_SYNC"
                elif count < row["event_count"] or (
                    count == row["event_count"] and head != row["head"]
                ):
                    status = "JOURNAL_ROLLBACK_OR_FORK_STOP"
                elif ancestor != row["head"]:
                    status = "JOURNAL_FORK_STOP"
                elif tick < row["tick"]:
                    status = "JOURNAL_TICK_REGRESSION_STOP"
                elif count == row["event_count"] + 1:
                    status = "ONE_EXTRA_EVENT_AMBIGUOUS_STOP"
                else:
                    status = "MULTIPLE_EXTRA_EVENTS_STOP"
                return _report(
                    row, classification=status,
                    count=count, tick=tick, head=head,
                    common_anchor=(ancestor == row["head"]),
                )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only local Aquarium/authority divergence report; no repair",
    )
    parser.add_argument("--journal", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--organism-id", required=True)
    parser.add_argument("--writer", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
    args = parser.parse_args(argv)
    try:
        result = inspect_recovery(
            args.journal, args.registry,
            json.loads(args.genome.read_text(encoding="utf-8")),
            json.loads(args.graph.read_text(encoding="utf-8")),
            organism_id=args.organism_id, writer=args.writer,
            expected_epoch=args.epoch, expected_generation=args.generation,
            aquarium_options={"neural_mode": args.neural_mode},
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["classification"] == "IN_SYNC" else 3


if __name__ == "__main__":
    raise SystemExit(main())
