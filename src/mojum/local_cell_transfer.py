"""Replay-verified, explicit transfers between historical local-cell policies."""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

from .aquarium import (
    Aquarium, AquariumError, MAX_REPLAY_EVENTS, V2A_ARCHIVE_MODE, V2B_MODE, V2B_WIRE_MODE, V3_MODE,
)
from .cell_policy_v2 import SCHEMA as V2B_SCHEMA
from .cell_policy_v3 import SCHEMA as V3_SCHEMA
from .archived_policy_v2a import SCHEMA as V2A_SCHEMA
from .local_cell_sense import STEP_METERS
from .noosphere import LocalNoosphere, NoosphereError
from .sim import canonical
from .world_room import Room


MAX_TRANSFER_INPUT_BYTES = 16 * 1024 * 1024


def _read_transfer_json(path):
    """Read a bounded regular file without blocking on POSIX special files."""
    if os.name == "posix" and not getattr(os, "O_NONBLOCK", 0):
        raise TransferError("cannot safely read transfer input without O_NONBLOCK")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TransferError("transfer input must be a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(MAX_TRANSFER_INPUT_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > MAX_TRANSFER_INPUT_BYTES:
        raise TransferError("transfer input exceeds byte limit")
    return json.loads(raw.decode("utf-8"))


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class _Transfer:
    schema: str
    source_mode: str
    source_schema: str
    target_mode: str
    target_schema: str
    max_ticks: int


V2A_TO_V2B = _Transfer(
    "mojum.migration.local-cell-v2a-to-v2b/1", V2A_ARCHIVE_MODE, V2A_SCHEMA,
    V2B_MODE, V2B_SCHEMA, 1000,
)
V2B_TO_V3 = _Transfer(
    "mojum.migration.local-cell-v2b-to-v3/1", V2B_MODE, V2B_SCHEMA,
    V3_MODE, V3_SCHEMA, MAX_REPLAY_EVENTS - 1,
)


class TransferError(ValueError):
    """Unsupported or unequal versioned experience or unsafe target."""


def _reject_destination_sidecars(destination):
    # An absent main file can still have another journal's WAL or rollback
    # data. Never publish beside or remove those independently owned entries.
    if any(os.path.lexists(str(destination) + suffix)
           for suffix in ("-wal", "-shm", "-journal")):
        raise TransferError("destination has existing SQLite sidecars; choose a new path")


def _anchor(value):
    if (type(value) is not str or len(value) != 64
            or any(ch not in _HEX for ch in value)):
        raise TransferError("provide an exact 64-character trusted source head")
    return value


def _source_manifest(path, transfer):
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
            or manifest.get("developmental_schema") != transfer.source_schema
            or manifest.get("exploration_mode") != "goal_free"
            or manifest.get("sensory_mode", "none") != "none"):
        raise TransferError("source has an incompatible bootstrap")
    if transfer.source_mode == V2A_ARCHIVE_MODE:
        if (manifest.get("motor_policy") != V2B_WIRE_MODE
                or manifest.get("motor_policy_schema") != V2A_SCHEMA
                or "developmental_policy" in manifest):
            raise TransferError("source is missing its v2A wire selector")
    elif (manifest.get("developmental_policy") != V2B_WIRE_MODE
          or "motor_policy" in manifest or "motor_policy_schema" in manifest):
        raise TransferError("source is missing its v2B wire selector")
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
                "room numeric scalar types are not preserved in the older bootstrap; "
                "only canonical default point-room is safely transferable")
        agent_id = manifest["agent_id"]
        dt_ns = manifest["dt_ns"]
        neural_mode = manifest["neural_mode"]
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise TransferError("invalid archived room, agent or clock") from exc
    return manifest, room, start, agent_id, dt_ns, neural_mode


def _normalized_frame(frame, *, expect_v3=False):
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
        if expect_v3:
            learning = development["learning"]
            neutral_retention = {
                "evicted_cell": None, "evicted_observations": 0,
                "total_evictions": 0, "total_forgotten_observations": 0,
            }
            for field, value in neutral_retention.items():
                if canonical(learning.pop(field)) != canonical(value):
                    raise TransferError("v3 retention changed pre-transfer evidence")
    except (KeyError, TypeError) as exc:
        raise TransferError("unsupported developmental frame envelope") from exc
    return normalized


def _matching_policy_states(source, target, transfer):
    if (source.get("schema") != transfer.source_schema
            or target.get("schema") != transfer.target_schema
            or target.get("cell_size_m") != STEP_METERS):
        raise TransferError("unexpected policy state version")
    if transfer.target_mode == V3_MODE and (
            target.get("evictions") != 0 or target.get("forgotten_observations") != 0):
        raise TransferError("target discarded pre-transfer observations")
    for key in ("last_tick", "updates", "records"):
        if canonical(source.get(key)) != canonical(target.get(key)):
            raise TransferError("policy experience differs at " + key)


def _matching_checkpoints(source, target, transfer):
    # Different bootstrap and journal event schemas MUST have new hashes.
    omitted = {"manifest_sha256", "journal_head", "developmental_state"}
    left = {key: value for key, value in source.items() if key not in omitted}
    right = {key: value for key, value in target.items() if key not in omitted}
    if canonical(left) != canonical(right):
        raise TransferError("non-policy organism checkpoint differs after conversion")
    _matching_policy_states(source["developmental_state"],
                            target["developmental_state"], transfer)


def _verify_source(path, agent_id, expected_head, count):
    with LocalNoosphere(path, agent_id, read_only=True) as archive:
        verified = archive.verify_chain(external_expected_head=expected_head)
        if verified["count"] != count or archive.count() != count:
            raise TransferError("source changed during translation")


def _verify_new_journal(path, *, seed, graph, room, start, agent_id, dt_ns,
                        neural_mode, motor_mode, count, head, expected_checkpoint):
    with Aquarium(
            path, seed, graph, room=room, start=start, agent_id=agent_id,
            dt_ns=dt_ns, neural_mode=neural_mode,
            motor_mode=motor_mode,
            external_expected_head=head) as reopened:
        if (reopened.store.count() != count
                or reopened.store.head() != head):
            raise TransferError("destination cold replay event count/head differs")
        if reopened.checkpoint() != expected_checkpoint:
            raise TransferError("destination cold replay checkpoint differs")


def transfer_journal(
        source, destination, *, transfer, expected_source_head, seed, graph,
        max_ticks, create_new_journal=False):
    """Build and exclusively publish a *new* journal, never overwrite source.

    Caller must explicitly authorize new-journal creation and independently
    provide the trusted original head. No auto-migration or background work.
    """
    if create_new_journal is not True:
        raise TransferError("explicit create_new_journal=True is required")
    anchor = _anchor(expected_source_head)
    if type(max_ticks) is not int or not 1 <= max_ticks <= transfer.max_ticks:
        raise TransferError(f"max_ticks must be within 1..{transfer.max_ticks}")
    source, dest = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise TransferError("source must be an existing nonsymlink SQLite file")
    if dest.is_symlink() or dest.exists():
        raise TransferError("destination must not already exist")
    if not dest.name or not dest.parent.is_dir():
        raise TransferError("destination parent must already exist")
    _reject_destination_sidecars(dest)
    if source.resolve() == dest.resolve():
        raise TransferError("source and destination paths must differ")
    if not isinstance(seed, dict) or not isinstance(graph, dict):
        raise TransferError("exact archived genome and graph objects are required")

    manifest, room, start, agent_id, dt_ns, neural_mode = _source_manifest(source, transfer)
    if manifest.get("neural_mode") not in ("shadow", "disabled"):
        raise TransferError("unsupported archived neural policy")
    with tempfile.TemporaryDirectory(
            dir=dest.parent, prefix=".mojum-local-cell-convert-") as temp_dir:
        temp = Path(temp_dir)
        working = temp / "working.sqlite3"
        staged = temp / "verified.sqlite3"

        with Aquarium(
                source, seed, graph, agent_id=agent_id, room=room,
                start=start, dt_ns=dt_ns, neural_mode=neural_mode,
                motor_mode=transfer.source_mode, read_only=True,
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
                    motor_mode=transfer.target_mode,
                    pulse_sum=archived._pulse_sum if neural_mode == "shadow" else "auto",
            ) as replacement:
                matched = 0
                entries = archived.store.dataset(
                    include_nonpublic=True, limit=count,
                )["projection"]["items"]
                for entry in entries:
                    if entry["seq"] == 1:
                        if entry["event"]["topic"] != "aquarium.bootstrap":
                            raise TransferError("first event is not bootstrap")
                        continue
                    if (entry["seq"] != matched + 2
                            or entry["event"]["topic"] != "aquarium.frame"):
                        raise TransferError("only contiguous goal-free frame events supported")
                    original = entry["event"]["payload"]
                    replayed = replacement.step()
                    before = _normalized_frame(original)
                    after = _normalized_frame(replayed, expect_v3=transfer.target_mode == V3_MODE)
                    if canonical(before) != canonical(after):
                        raise TransferError(f"source and destination observations diverge at frame {matched}")
                    matched += 1
                if matched != ticks:
                    raise TransferError("missing source frames")
                original_checkpoint = archived.checkpoint()
                new_checkpoint = replacement.checkpoint()
                _matching_checkpoints(original_checkpoint, new_checkpoint, transfer)
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
            motor_mode=transfer.target_mode, count=new_count, head=dest_head, expected_checkpoint=new_checkpoint)
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
            motor_mode=transfer.target_mode, count=new_count, head=dest_head, expected_checkpoint=new_checkpoint)
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
        _reject_destination_sidecars(dest)
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
        "schema": transfer.schema,
        "status": "EXPLICIT_VERIFIED_NEW_JOURNAL",
        "source_policy_schema": transfer.source_schema,
        "destination_policy_schema": transfer.target_schema,
        "source_head": anchor, "destination_head": dest_head,
        "source_events": count, "destination_events": new_count,
        "compared_frames": ticks,
        "source_writes": 0,
        "source_original_journal_retained": True,
        "event_hashes_are_expected_to_differ": True,
        "external_head_is_supplied_not_authenticated_here": True,
        "not_a_general_migration_or_neural_model": True,
    }


def transfer_cli(transfer, argv=None):
    parser = argparse.ArgumentParser(description="Replay a verified local-cell journal into a new format")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--genome", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument("--create-new-journal", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=transfer.max_ticks)
    args = parser.parse_args(argv)
    try:
        seed = _read_transfer_json(args.genome)
        graph = _read_transfer_json(args.graph)
        result = transfer_journal(
            args.source, args.destination, transfer=transfer,
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
