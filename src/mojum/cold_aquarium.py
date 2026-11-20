"""Cold, CPU-first Aquarium journal replay across processes, without live migration.

Exports a quiescent *synthetic Aquarium fixture* into the existing five-part
checkpoint envelope, then recreates a **new** SQLite journal and replays the
source's deterministic GENESIS, shadow learner and point-room state.

Not encryption, source authentication, physics, live migration, distributed
consensus, or a hot standby. The target never inherits motor capabilities.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from .aquarium import Aquarium, MAX_REPLAY_EVENTS
from .checkpoint_bundle import NAMES, pack
from .checkpoint_handoff import export_new_archive, read_verified_archive
from .genesis import GenomeLedger
from .neural import PulsePredictor
from .noosphere import LocalNoosphere
from .world_room import Room

WORLD_SCHEMA = "mojum.cold-replay.world/1"
MEMORY_SCHEMA = "mojum.cold-replay.memory/1"
NEURAL_SCHEMA = "mojum.cold-replay.neural/1"
BODY_SCHEMA = "mojum.cold-replay.body-disabled/1"


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _parse_component(raw, schema, required):
    if type(raw) is not bytes:
        raise ValueError("component must be bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate component JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               ValueError("invalid component JSON constant")))
    except (UnicodeError, json.JSONDecodeError, TypeError, RecursionError) as exc:
        raise ValueError("invalid component JSON") from exc
    if type(value) is not dict or set(value) != required or value.get("schema") != schema:
        raise ValueError("unsupported cold replay component schema")
    if raw != _canonical_bytes(value):
        raise ValueError("noncanonical cold replay component JSON")
    return value


def capture_quiescent_aquarium(aquarium):
    """Capture fixture state and the complete PRIVATE event journal.

    The caller must pause all Aquarium writers. Pre/post checks detect simple
    in-process mutations; they are not a distributed atomic snapshot barrier.
    """
    if not isinstance(aquarium, Aquarium):
        raise ValueError("expected a running Aquarium")
    before = aquarium.checkpoint()
    chain = aquarium.store.verify_chain(external_expected_head=before["journal_head"])
    if not 1 <= chain["count"] <= MAX_REPLAY_EVENTS:
        raise ValueError("journal event count out of bounds")
    items = aquarium.store.dataset(include_nonpublic=True,
                                   limit=MAX_REPLAY_EVENTS)["projection"]["items"]
    ledger = aquarium.genome.snapshot()
    learner = aquarium.learner.snapshot() if aquarium.learner is not None else None
    position = {
        "tick": aquarium.world.tick,
        "x": aquarium.world.x,
        "y": aquarium.world.y,
        "revision": aquarium.world.revision,
    }
    after = aquarium.checkpoint()
    if (before != after or chain["count"] != len(items)
            or aquarium.store.head() != chain["head"]
            or after["tick"] != position["tick"]
            or before["genome_revision"] != ledger["revision"]
            or before["world_revision"] != position["revision"]
            or (after["neural_state"] != learner)):
        raise ValueError("source advanced during cold snapshot; pause writers")
    parts = {
        "genome": _canonical_bytes(ledger),
        "neural": _canonical_bytes({"schema": NEURAL_SCHEMA, "mode": aquarium.neural_mode,
                                    "state": learner}),
        "body": _canonical_bytes({"schema": BODY_SCHEMA,
                                  "status": "NOT_CAPTURED_ACTUATORS_DISABLED"}),
        "world": _canonical_bytes({
            "schema": WORLD_SCHEMA, "graph": aquarium.graph,
            "manifest": aquarium.manifest, "position": position,
            "checkpoint": before,
        }),
        "memory": _canonical_bytes({
            "schema": MEMORY_SCHEMA, "head": chain["head"],
            "count": chain["count"], "items": items,
        }),
    }
    if set(parts) != set(NAMES):
        raise ValueError("missing checkpoint component")
    # The bundle itself is bounded by checkpoint_bundle.pack.
    return pack(aquarium.agent_id, aquarium.tick, parts)


def export_quiescent_aquarium(aquarium, archive_path):
    """Export a paused Aquarium to a new private archive; never overwrite."""
    bundle = capture_quiescent_aquarium(aquarium)
    # Reuse PR #48's bounded, no-clobber publication instead of another
    # competing local file transport implementation.
    destination = Path(archive_path)
    if not destination.parent.is_dir():
        raise ValueError("archive parent directory must exist")
    with tempfile.TemporaryDirectory(prefix=".mojum-capture-", dir=destination.parent) as scratch:
        files = {}
        for name in NAMES:
            path = Path(scratch) / (name + ".bin")
            path.write_bytes(_component_from_bundle(bundle, name))
            files[name] = path
        result = export_new_archive(
            aquarium.agent_id, aquarium.tick, files, destination,
        )
    return result


def _component_from_bundle(bundle, name):
    # Never bypass bundle integrity when recovering decoded component bytes.
    from .checkpoint_bundle import unpack
    return unpack(bundle, expected_revision=bundle["manifest"]["revision"])[name]


def _validated_payloads(parts, *, expected_id, expected_tick):
    genome = _parse_component(parts["genome"], "mojum.genesis.ledger/1",
                              {"schema", "seed", "events", "revision"})
    checked_ledger = GenomeLedger.from_snapshot(genome)
    if genome["seed"]["genome_id"] != expected_id:
        raise ValueError("genome and expected organism do not match")
    neural = _parse_component(parts["neural"], NEURAL_SCHEMA,
                              {"schema", "mode", "state"})
    if neural["mode"] not in ("shadow", "disabled"):
        raise ValueError("unsupported cold replay neural mode")
    if neural["mode"] == "disabled":
        if neural["state"] is not None:
            raise ValueError("disabled neural mode has a nonempty state")
    elif PulsePredictor.from_snapshot(neural["state"]).snapshot() != neural["state"]:
        raise ValueError("inconsistent neural snapshot")
    body = _parse_component(parts["body"], BODY_SCHEMA, {"schema", "status"})
    if body["status"] != "NOT_CAPTURED_ACTUATORS_DISABLED":
        raise ValueError("cold replay cannot inherit motor capability")
    world = _parse_component(parts["world"], WORLD_SCHEMA,
                             {"schema", "graph", "manifest", "position", "checkpoint"})
    memory = _parse_component(parts["memory"], MEMORY_SCHEMA,
                              {"schema", "head", "count", "items"})
    checkpoint = world["checkpoint"]
    manifest = world["manifest"]
    position = world["position"]
    if (type(checkpoint) is not dict or type(manifest) is not dict
            or type(position) is not dict or type(memory["items"]) is not list
            or type(memory["count"]) is not int
            or not (1 <= memory["count"] <= MAX_REPLAY_EVENTS)
            or memory["count"] != len(memory["items"])
            or type(position.get("tick")) is not int
            or position["tick"] != expected_tick
            or checkpoint.get("agent_id") != expected_id
            or checkpoint.get("tick") != expected_tick
            or checkpoint.get("journal_head") != memory["head"]
            or checkpoint.get("genome_revision") != checked_ledger.revision
            or checkpoint.get("world_revision") != position.get("revision")
            or checkpoint.get("neural_state") != neural["state"]
            or checkpoint.get("neural_mode") != neural["mode"]
            or manifest.get("agent_id") != expected_id
            or manifest.get("neural_mode") != neural["mode"]):
        raise ValueError("inconsistent cold replay components")
    room_value = manifest.get("room")
    if (type(room_value) is not dict or set(room_value) !=
            {"width", "height", "obstacles", "start"}):
        raise ValueError("invalid room manifest")
    if (type(room_value["obstacles"]) is not list
            or type(room_value["start"]) is not list
            or len(room_value["start"]) != 2
            or any(type(o) is not list or len(o) != 4
                   for o in room_value["obstacles"])):
        raise ValueError("invalid room geometry")
    try:
        room = Room(room_value["width"], room_value["height"],
                    tuple(tuple(x) for x in room_value["obstacles"]))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid room geometry") from exc
    if type(manifest.get("dt_ns")) is not int:
        raise ValueError("invalid room clock")
    return genome, neural, world, memory, room


def restore_cold_aquarium(archive, destination_db, *, expected_organism_id,
                          expected_tick, expected_manifest_sha256=None):
    """Rebuild a fresh SQLite journal and verify complete deterministic replay.

    Never overwrites a database. Caller must use a trusted, single-writer
    destination parent; no hot swap, no actuator import, no power-loss claims.
    """
    if type(expected_tick) is not int or expected_tick < 0:
        raise ValueError("expected_tick must be a nonnegative integer")
    manifest, parts = read_verified_archive(
        archive, expected_organism_id=expected_organism_id,
        expected_revision=expected_tick,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    genome, neural, world, memory, room = _validated_payloads(
        parts, expected_id=expected_organism_id, expected_tick=expected_tick,
    )
    target = Path(destination_db)
    if not target.name or not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("new database needs an existing trusted parent")
    if os.path.lexists(target):
        raise ValueError("destination database already exists")
    fd, stage = tempfile.mkstemp(prefix=".mojum-cold-", suffix=".sqlite3",
                                  dir=target.parent)
    os.close(fd)
    try:
        with LocalNoosphere(stage, expected_organism_id) as store:
            for index, entry in enumerate(memory["items"], 1):
                if (type(entry) is not dict or set(entry) !=
                        {"seq", "event", "event_hash"} or
                        type(entry["seq"]) is not int or entry["seq"] != index):
                    raise ValueError("noncontiguous archived Noosphere events")
                new_head = store.append(entry["event"], expected_head=store.head())
                if new_head != entry["event_hash"]:
                    raise ValueError("archived Noosphere event digest mismatch")
            store.verify_chain(external_expected_head=memory["head"])
        clock = world["manifest"]
        with Aquarium(
            stage, genome["seed"], world["graph"],
            agent_id=expected_organism_id, room=room,
            start=tuple(clock["room"]["start"]),
            dt_ns=clock["dt_ns"], neural_mode=neural["mode"],
            external_expected_head=memory["head"],
        ) as restored:
            got = restored.checkpoint()
            expected = world["checkpoint"]
            current_pos = {
                "tick": restored.world.tick, "x": restored.world.x,
                "y": restored.world.y, "revision": restored.world.revision,
            }
            current_neural = (restored.learner.snapshot()
                              if restored.learner is not None else None)
            if (got != expected or current_pos != world["position"]
                    or restored.genome.snapshot() != genome
                    or current_neural != neural["state"]
                    or restored.store.count() != memory["count"]):
                raise ValueError("cold replay differs from committed source")
        # Atomic no-clobber filename publication on hardlink-capable local FS.
        # No trusted directory fsync, anti-rollback or adversarial-writer safety.
        try:
            os.link(stage, target)
        except FileExistsError as exc:
            raise ValueError("destination database already exists") from exc
        return got
    finally:
        for file in (stage, stage + "-wal", stage + "-shm"):
            try:
                os.unlink(file)
            except FileNotFoundError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mojum cold replay of synthetic Aquarium (offline/no motor)",
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    export = sub.add_parser("export")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--genome", type=Path, required=True)
    export.add_argument("--graph", type=Path, required=True)
    export.add_argument("--organism-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
    restore = sub.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--db", type=Path, required=True)
    restore.add_argument("--organism-id", required=True)
    restore.add_argument("--tick", type=int, required=True)
    restore.add_argument("--manifest-sha256")
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            seed = json.loads(args.genome.read_text(encoding="utf-8"))
            graph = json.loads(args.graph.read_text(encoding="utf-8"))
            with Aquarium(args.db, seed, graph, agent_id=args.organism_id,
                          neural_mode=args.neural_mode) as q:
                manifest = export_quiescent_aquarium(q, args.output)
            summary = {
                "status": "SYNTHETIC_COLD_ARCHIVE_NOT_LIVE",
                "organism_id": manifest["organism_id"],
                "tick": manifest["revision"],
                "manifest_sha256": manifest["manifest_sha256"],
            }
        else:
            restored = restore_cold_aquarium(
                args.archive, args.db, expected_organism_id=args.organism_id,
                expected_tick=args.tick, expected_manifest_sha256=args.manifest_sha256,
            )
            summary = {
                "status": "SYNTHETIC_COLD_REPLAY_VERIFIED_NO_MOTOR",
                "organism_id": restored["agent_id"],
                "tick": restored["tick"],
                "journal_head": restored["journal_head"],
            }
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
