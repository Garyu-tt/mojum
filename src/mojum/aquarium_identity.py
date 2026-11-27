"""Inspect or assign a human-declared name in an existing Aquarium journal.

This local CLI reuses the canonical identity event. Actor declarations are not
authenticated, and naming does not advance simulation or developmental time.
"""

from __future__ import annotations

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import stat

from ._json_nesting import MAX_JSON_DEPTH, exceeds_json_depth
from .aquarium import Aquarium, AquariumError, MOTOR_MODES, _safe_event_text, load_cli_room
from .body_genetics import IDENTITY_SCHEMA, validate_human_name
from .genesis import validate_seed
from .graph import validate as validate_graph
from .sensory_play import ToyScene


SCHEMA = "mojum.aquarium.identity-cli/1"
MAX_CONFIG_BYTES = 256 * 1024


def _expected_head(value, *, required=False):
    if value is None and not required:
        return None
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("expected head must be a lowercase SHA-256 digest")
    return value


def _journal_source(path):
    candidate = Path(path)
    if candidate.is_symlink() or not stat.S_ISREG(candidate.stat().st_mode):
        raise ValueError("identity commands require an existing regular journal")
    return candidate


def _receipt(aquarium, action):
    count = aquarium.stored_events
    checkpoint = aquarium.checkpoint()
    return {
        "schema": SCHEMA,
        "action": action,
        **{key: checkpoint[key] for key in (
            "agent_id", "tick", "sim_time_ns", "development_months",
            "journal_head", "display_name", "name_provenance", "trust",
        )},
        "stored_events": count,
    }


def _can_assign(aquarium):
    if aquarium.motor_mode not in ("scripted", "developmental_fixture"):
        raise AquariumError("local-cell journals do not support name assignment")
    if aquarium.identity is not None:
        raise AquariumError("agent already named; renaming is not implemented")
    if aquarium.stored_events >= aquarium.journal_event_budget:
        raise AquariumError("name assignment exceeds remaining journal event budget")


def identity_status(db, seed, graph, *, expected_head=None, **runtime):
    """Replay committed history read-only and return its identity metadata."""
    head = _expected_head(expected_head)
    seed = validate_seed(seed)
    source = _journal_source(db)
    with Aquarium(source, seed, graph, agent_id=seed["genome_id"],
                  external_expected_head=head, read_only=True, **runtime) as aquarium:
        return _receipt(aquarium, "status")


def assign_identity(db, seed, graph, *, display_name, actor_id, expected_head,
                    **runtime):
    """Append one existing naming event after read-only, head-pinned admission."""
    head = _expected_head(expected_head, required=True)
    seed = validate_seed(seed)
    identity = validate_human_name({
        "schema": IDENTITY_SCHEMA,
        "agent_id": seed["genome_id"],
        "display_name": display_name,
        "assigned_by": "human",
    }, seed["genome_id"])
    actor_id = _safe_event_text(actor_id, "human actor id", 128)
    try:
        actor_id.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("human actor id must be valid UTF-8") from exc
    source = _journal_source(db)
    with Aquarium(source, seed, graph, agent_id=seed["genome_id"],
                  external_expected_head=head, read_only=True, **runtime) as aquarium:
        _can_assign(aquarium)
        admitted_head = aquarium.checkpoint()["journal_head"]
    # A removed source cannot be bootstrapped, and a competing writer changes
    # the required head. The canonical append CAS remains authoritative.
    with Aquarium(source, seed, graph, agent_id=seed["genome_id"],
                  external_expected_head=admitted_head, **runtime) as aquarium:
        _can_assign(aquarium)
        aquarium.assign_human_name(identity, actor_id=actor_id)
        return _receipt(aquarium, "assign")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("configuration JSON numbers must be finite")


def _load_config(path):
    # Files supplied by the caller must not block on a FIFO or device. Package
    # resources also support Traversable objects from a zipped installation.
    if isinstance(path, Path):
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("configuration must be a regular JSON file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("configuration must be a regular JSON file")
            # Keep ownership here even if wrapping the raw descriptor fails.
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read(MAX_CONFIG_BYTES + 1)
        finally:
            os.close(descriptor)
    else:
        with path.open("rb") as stream:
            payload = stream.read(MAX_CONFIG_BYTES + 1)
    if len(payload) > MAX_CONFIG_BYTES:
        raise ValueError("configuration exceeds the 256 KiB byte limit")
    raw = payload.decode("utf-8", errors="strict")
    if exceeds_json_depth(raw, MAX_JSON_DEPTH):
        raise ValueError("configuration JSON nesting depth exceeded")
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect or assign a human-declared Aquarium name in existing history",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    packaged = files("mojum").joinpath("data")
    for action in ("status", "assign"):
        command = actions.add_parser(action)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--genome", type=Path,
                             default=packaged.joinpath("genesis_seed.json"))
        command.add_argument("--graph", type=Path,
                             default=packaged.joinpath("first_loop.json"))
        command.add_argument("--expected-head", required=action == "assign")
        command.add_argument("--room-file", type=Path)
        command.add_argument("--dt-ns", type=int, default=10_000_000)
        command.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
        command.add_argument("--motor-mode", choices=MOTOR_MODES, default="scripted")
        command.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                             default="placeholder")
        command.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"),
                             default="none")
        command.add_argument("--sensory-scene", choices=("default", "empty"), default="default")
        command.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none")
        command.add_argument("--pulse-sum", choices=("auto", "ordered", "compensated"), default="auto")
        if action == "assign":
            command.add_argument("--display-name", required=True)
            command.add_argument("--actor-id", required=True)
    args = parser.parse_args(argv)
    try:
        _expected_head(args.expected_head, required=args.action == "assign")
        seed = validate_seed(_load_config(args.genome))
        graph = _load_config(args.graph)
        validate_graph(graph)
        room, start = load_cli_room(args.room_file, args.motor_mode)
        scene = None
        if args.sensory_scene == "empty":
            if args.sensory_mode == "none":
                raise ValueError("--sensory-scene empty requires a sensory mode")
            scene = ToyScene(width=room.width if room is not None else 4.0,
                             height=room.height if room is not None else 4.0, toys=())
        runtime = dict(room=room, start=start, dt_ns=args.dt_ns,
                       neural_mode=args.neural_mode, motor_mode=args.motor_mode,
                       brain_mode=args.brain_mode, sensory_mode=args.sensory_mode,
                       sensory_scene=scene, temporal_mode=args.temporal_mode,
                       pulse_sum=args.pulse_sum)
        if args.action == "status":
            receipt = identity_status(args.db, seed, graph,
                                      expected_head=args.expected_head, **runtime)
        else:
            receipt = assign_identity(args.db, seed, graph,
                                      display_name=args.display_name, actor_id=args.actor_id,
                                      expected_head=args.expected_head, **runtime)
    except (ValueError, OSError, sqlite3.Error, RecursionError) as exc:
        parser.error(str(exc))
    # Names and organism IDs may contain Unicode on an ASCII/CP1251 terminal.
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
