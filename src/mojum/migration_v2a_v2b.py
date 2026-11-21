"""Explicit, bounded R0.14 v2A -> v2B synthetic Aquarium journal re-expression.

NEVER opens the source SQLite database for writing, mutates old events, or
silently changes policy versions. Destination is a fresh independent journal
published only after deterministic complete-frame equivalence and cold replay.
This is a research/QA candidate, not an automatic migration facility.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from .aquarium import Aquarium, ARCHIVE_MODE, AquariumError, V2B_MODE
from .cell_policy_v2 import SCHEMA as TARGET_SCHEMA
from .archived_policy_v2a import SCHEMA as SOURCE_SCHEMA
from .developmental_policy import STEP_METERS
from .noosphere import LocalNoosphere, NoosphereError
from .sim import canonical
from .world_room import Room


SCHEMA = "mojum.migration.local-cell-v2a-to-v2b/1"
MAX_TRANSFER_TICKS = 1000
_HEX = frozenset("0123456789abcdef")


class TransferError(ValueError):
    """Unsupported or unequal versioned experience or unsafe target."""


def _anchor(value):
    if (type(value) is not str or len(value) != 64
            or any(ch not in _HEX for ch in value)):
        raise TransferError("provide an exact 64-character trusted source head")
    return value


def _source_manifest(path):
    # This is untrusted bootstrap *discovery*, NEVER a verification result.
    # The old Aquarium archive will replay and check every byte, owner,
    # timestamp and event hash against an independently supplied head.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        row = db.execute("SELECT wire_json FROM events WHERE seq = 1").fetchone()
    if row is None or type(row[0]) is not str:
        raise TransferError("source journal has no recognizable bootstrap")
    try:
        event = json.loads(row[0])
        manifest = event["payload"]
    except (ValueError, KeyError, TypeError) as exc:
        raise TransferError("invalid source bootstrap") from exc
    if (type(manifest) is not dict
            or manifest.get("schema") != "mojum.aquarium.bootstrap/1"
            or manifest.get("developmental_schema") != SOURCE_SCHEMA
            or manifest.get("motor_policy") != "local_cell_v2"
            or manifest.get("motor_policy_schema") != SOURCE_SCHEMA
            or "developmental_policy" in manifest
            or manifest.get("exploration_mode") != "goal_free"
            or manifest.get("sensory_mode", "none") != "none"):
        raise TransferError("source must be unsensored canonical v2A goal-free journal")
    try:
        config = manifest["room"]
        if (type(config) is not dict
                or set(config) != {"width", "height", "obstacles", "start"}
                or type(config["obstacles"]) is not list
                or type(config["start"]) is not list
                or len(config["start"]) != 2):
            raise TransferError("unsupported room bootstrap contract")
        # R0.13/R0.14 manifests coerce width/height to floats and preserve
        # obstacle numbers as provided. The *exact* legacy room revision,
        # however, hashes original Python int/float representations.
        # Reconstructing arbitrary numeric room values as floats silently
        # changes that revision even when the geometry looks identical.
        # Until the bootstrap pins exact scalar types, support ONLY the
        # canonical original built-in point-room fixture, byte-for-byte.
        room = Room(4, 4, ((2, 1, .1, 2),))
        start = (1.0, 2.0)
        expected_room = {
            "width": 4.0, "height": 4.0,
            "obstacles": [[2, 1, 0.1, 2]],
            "start": [1.0, 2.0],
        }
        if config != expected_room:
            raise TransferError(
                "room numeric scalar types are not preserved in v2A bootstrap; "
                "only canonical default point-room is safely transferable")
        agent_id = manifest["agent_id"]
        dt_ns = manifest["dt_ns"]
        neural_mode = manifest["neural_mode"]
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise TransferError("invalid archived room, agent or clock") from exc
    return manifest, room, start, agent_id, dt_ns, neural_mode


def _normalized_frame(frame):
    # Exclude only the *intentionally renamed* policy wire labels and the
    # hash of the differently serialized policy snapshot. Everything else
    # (including proprioception, motor action, measured collision, world,
    # heartbeat, phenotype, neural observer and revision) must be identical.
    normalized = copy.deepcopy(frame)
    try:
        development = normalized["developmental"]
        del development["schema"]
        del development["policy_sha256"]
        del development["intent"]["source"]
        del development["learning"]["schema"]
        del development["learning"]["source"]
    except (KeyError, TypeError) as exc:
        raise TransferError("unsupported developmental frame envelope") from exc
    return normalized


def _matching_policy_states(source, target):
    if (source.get("schema") != SOURCE_SCHEMA
            or target.get("schema") != TARGET_SCHEMA
            or target.get("cell_size_m") != STEP_METERS):
        raise TransferError("unexpected policy state version")
    for key in ("last_tick", "updates", "records"):
        if canonical(source.get(key)) != canonical(target.get(key)):
            raise TransferError("policy experience differs at " + key)


def _matching_checkpoints(source, target):
    # Different bootstrap and journal event schemas MUST have new hashes.
    omitted = {"manifest_sha256", "journal_head", "developmental_state"}
    left = {key: value for key, value in source.items() if key not in omitted}
    right = {key: value for key, value in target.items() if key not in omitted}
    if canonical(left) != canonical(right):
        raise TransferError("non-policy organism checkpoint differs after conversion")
    _matching_policy_states(source["developmental_state"],
                            target["developmental_state"])


def _verify_source(path, agent_id, expected_head, count):
    with LocalNoosphere(path, agent_id, read_only=True) as archive:
        verified = archive.verify_chain(external_expected_head=expected_head)
        if verified["count"] != count or archive.count() != count:
            raise TransferError("source changed during translation")


def _verify_new_journal(path, *, seed, graph, room, start, agent_id, dt_ns,
                        neural_mode, count, head, expected_checkpoint):
    with Aquarium(
            path, seed, graph, room=room, start=start, agent_id=agent_id,
            dt_ns=dt_ns, neural_mode=neural_mode,
            exploration_mode="goal_free", sensory_mode="none",
            developmental_policy=V2B_MODE,
            external_expected_head=head) as reopened:
        if (reopened.store.count() != count
                or reopened.store.head() != head):
            raise TransferError("destination cold replay event count/head differs")
        if reopened.checkpoint() != expected_checkpoint:
            raise TransferError("destination cold replay checkpoint differs")


def transfer_v2a_to_v2b(
        source, destination, *, expected_source_head, seed, graph,
        create_new_journal=False, max_ticks=MAX_TRANSFER_TICKS):
    """Build and exclusively publish a *new* journal, never overwrite source.

    Caller must explicitly authorize new-journal creation and independently
    provide the trusted original head. No auto-migration or background work.
    """
    if create_new_journal is not True:
        raise TransferError("explicit create_new_journal=True is required")
    anchor = _anchor(expected_source_head)
    if type(max_ticks) is not int or not 1 <= max_ticks <= MAX_TRANSFER_TICKS:
        raise TransferError("max_ticks must be within 1..1000")
    source, dest = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise TransferError("source must be an existing nonsymlink SQLite file")
    if dest.is_symlink() or dest.exists():
        raise TransferError("destination must not already exist")
    if not dest.name or not dest.parent.is_dir():
        raise TransferError("destination parent must already exist")
    if source.resolve() == dest.resolve():
        raise TransferError("source and destination paths must differ")
    if not isinstance(seed, dict) or not isinstance(graph, dict):
        raise TransferError("exact archived genome and graph objects are required")

    manifest, room, start, agent_id, dt_ns, neural_mode = _source_manifest(source)
    if manifest.get("neural_mode") not in ("shadow", "disabled"):
        raise TransferError("unsupported archived neural policy")
    with tempfile.TemporaryDirectory(
            dir=dest.parent, prefix=".mojum-r014-convert-") as temp_dir:
        temp = Path(temp_dir)
        working = temp / "new-v2b-working.sqlite3"
        staged = temp / "validated-v2b-publish.sqlite3"

        with Aquarium(
                source, seed, graph, agent_id=agent_id, room=room,
                start=start, dt_ns=dt_ns, neural_mode=neural_mode,
                exploration_mode="goal_free", sensory_mode="none",
                developmental_policy=ARCHIVE_MODE,
                external_expected_head=anchor) as archived:
            if archived.manifest != manifest:
                raise TransferError("untrusted bootstrap differs from replayed manifest")
            ticks, count = archived.tick, archived.store.count()
            if not 1 <= ticks <= max_ticks or count != ticks + 1:
                # No loss of GENESIS acquisitions, sampled sensory events, or
                # unexplained history is permitted. This release only handles
                # pure goal-free frame sequences inside its strict budget.
                raise TransferError("unsupported tick count or non-frame history")
            head = archived.checkpoint()["journal_head"]
            if head != anchor:
                raise TransferError("anchored source head mismatch")
            with Aquarium(
                    working, seed, graph, room=room, start=start,
                    agent_id=agent_id, dt_ns=dt_ns, neural_mode=neural_mode,
                    exploration_mode="goal_free", sensory_mode="none",
                    developmental_policy=V2B_MODE) as replacement:
                matched = 0
                for entry in archived.store.iter_events(include_nonpublic=True):
                    if entry["seq"] == 1:
                        if entry["event"]["topic"] != "aquarium.bootstrap":
                            raise TransferError("first event is not bootstrap")
                        continue
                    if (entry["seq"] != matched + 2
                            or entry["event"]["topic"] != "aquarium.frame"):
                        raise TransferError("only contiguous goal-free frame events supported")
                    original = entry["event"]["payload"]
                    replayed = replacement.step()
                    if canonical(_normalized_frame(original)) != canonical(_normalized_frame(replayed)):
                        raise TransferError(
                            "v2A and v2B observations diverge at frame " + str(matched))
                    matched += 1
                if matched != ticks:
                    raise TransferError("missing source frames")
                original_checkpoint = archived.checkpoint()
                new_checkpoint = replacement.checkpoint()
                _matching_checkpoints(original_checkpoint, new_checkpoint)
                dest_head = new_checkpoint["journal_head"]
                new_count = replacement.store.count()
                if new_count != count:
                    raise TransferError("target event count differs")
                replacement.store.verify_chain(external_expected_head=dest_head)
            if archived.store.head() != anchor or archived.store.count() != count:
                raise TransferError("source advanced while destination was being verified")

        # Reopen the independently generated destination from cold SQLite,
        # then copy it into a single WAL-independent SQLite main database.
        _verify_new_journal(
            working, seed=seed, graph=graph, room=room, start=start,
            agent_id=agent_id, dt_ns=dt_ns, neural_mode=neural_mode,
            count=new_count, head=dest_head, expected_checkpoint=new_checkpoint)
        with closing(sqlite3.connect(working.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as original_db:
            with closing(sqlite3.connect(staged)) as published_db:
                original_db.backup(published_db)
                published_db.commit()
        if os.name == "posix":
            staged.chmod(0o600)
        _verify_new_journal(
            staged, seed=seed, graph=graph, room=room, start=start,
            agent_id=agent_id, dt_ns=dt_ns, neural_mode=neural_mode,
            count=new_count, head=dest_head, expected_checkpoint=new_checkpoint)
        _verify_source(source, agent_id, anchor, count)
        if dest.exists() or dest.is_symlink():
            raise TransferError("destination appeared before exclusive publication")

        # os.link() is exclusive-create, never os.replace(): even if another
        # process creates a different output filename in this interval, this
        # migration will not overwrite it. Both paths are on the same FS.
        # Windows requires a writable descriptor for fsync. The validated
        # private staging file is opened without truncation and is not edited.
        with staged.open("r+b") as ready:
            os.fsync(ready.fileno())
        try:
            os.link(staged, dest)
        except FileExistsError as exc:
            raise TransferError("destination already exists; refusing overwrite") from exc
        except OSError as exc:
            raise TransferError("filesystem does not support safe exclusive publication") from exc
        if os.name == "posix":
            # If directory sync fails *after* linking, don't delete an output
            # that another process could already have opened: fail visibly.
            fd = os.open(dest.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    # Report is metadata only, not a signature or a complete memory archive.
    return {
        "schema": SCHEMA,
        "status": "EXPLICIT_VERIFIED_NEW_JOURNAL",
        "source_policy_schema": SOURCE_SCHEMA,
        "destination_policy_schema": TARGET_SCHEMA,
        "source_head": anchor, "destination_head": dest_head,
        "source_events": count, "destination_events": new_count,
        "compared_frames": ticks,
        "source_writes": 0,
        "source_original_journal_retained": True,
        "event_hashes_are_expected_to_differ": True,
        "external_head_is_supplied_not_authenticated_here": True,
        "not_a_general_migration_or_neural_model": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--genome", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument("--create-new-journal", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=MAX_TRANSFER_TICKS)
    args = parser.parse_args(argv)
    try:
        seed = json.loads(args.genome.read_text(encoding="utf-8"))
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
        result = transfer_v2a_to_v2b(
            args.source, args.destination,
            expected_source_head=args.expected_source_head,
            seed=seed, graph=graph,
            create_new_journal=args.create_new_journal,
            max_ticks=args.max_ticks)
    except (TransferError, AquariumError, NoosphereError, OSError,
            ValueError, TypeError, sqlite3.Error) as exc:
        print("TRANSFER ABORTED: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
