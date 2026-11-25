"""Installed, anchored access to the existing synthetic GENESIS transition.

No new gene semantics or persistence format: Aquarium.acquire remains the
writer. Status and acquisition preflight replay an existing journal read-only.
Receipts describe the committed prefix without exporting its private content.
"""

from __future__ import annotations

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import stat

from .aquarium import Aquarium, AquariumError, MOTOR_MODES, load_cli_room
from .genesis import ACQUISITION_SCHEMA, GENES, GenomeLedger, validate_seed
from .graph import load_graph
from .sensory_play import ToyScene


SCHEMA = "mojum.aquarium.genesis-command/1"
MAX_SEED_BYTES = 64 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("genome JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("genome JSON requires finite canonical numbers")


def _load_seed(path):
    # Do not introduce an unbounded or blocking file read in this new CLI.
    # open() owns cleanup even if fstat or JSON admission raises.
    def opener(filename, flags):
        return os.open(filename, flags | getattr(os, "O_NONBLOCK", 0))

    with open(path, "rb", opener=opener) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("genome must be a regular file")
        if info.st_size > MAX_SEED_BYTES:
            raise ValueError("genome exceeds the 64 KiB input limit")
        payload = stream.read(MAX_SEED_BYTES + 1)
    if len(payload) > MAX_SEED_BYTES:
        raise ValueError("genome exceeds the 64 KiB input limit")
    return validate_seed(json.loads(
        payload.decode("utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    ))


def _scene(selector, sensory_mode, room):
    # Match the explicit selector used by the installed lifecycle/export
    # workflow, without depending on helpers that are not yet on main.
    if selector == "default":
        return None
    if sensory_mode == "none":
        raise ValueError("--sensory-scene empty requires a toy sensory mode")
    return ToyScene(width=4.0 if room is None else room.width,
                    height=4.0 if room is None else room.height, toys=())


def _receipt(aquarium, command, *, previous_head=None):
    checkpoint = aquarium.checkpoint()
    events = aquarium.genome.snapshot()["events"]
    effective = sum(event["valid_from_tick"] <= aquarium.tick for event in events)
    result = {
        "schema": SCHEMA,
        "command": command,
        "agent_id": aquarium.agent_id,
        "tick": checkpoint["tick"],
        "sim_time_ns": checkpoint["sim_time_ns"],
        "journal_head": checkpoint["journal_head"],
        "stored_events": aquarium.stored_events,
        "genome_revision": checkpoint["genome_revision"],
        "acquisitions": len(events),
        "effective_acquisitions": effective,
        "pending_acquisitions": len(events) - effective,
        "classification": "PRIVATE_SYNTHETIC_METADATA_NOT_AUTHENTICATED",
    }
    if previous_head is not None:
        result["previous_journal_head"] = previous_head
    return result


def _prepare_acquisition(aquarium, args):
    if aquarium.stored_events >= aquarium.journal_event_budget:
        raise AquariumError("journal event budget reached")
    effective_tick = (aquarium.tick if args.valid_from_tick is None
                      else args.valid_from_tick)
    if effective_tick < aquarium.tick:
        raise AquariumError("cannot retroactively change committed frames")
    now = aquarium.tick * aquarium.dt_ns
    event = {
        "schema": ACQUISITION_SCHEMA,
        "event_id": args.event_id,
        "genome_id": aquarium.agent_id,
        "gene": args.gene,
        "delta": args.delta,
        "valid_from_tick": effective_tick,
        "observed_at_ns": now if args.observed_at_ns is None else args.observed_at_ns,
        "recorded_at_ns": now,
        "source": args.source,
        "evidence": args.evidence,
    }
    # The canonical ledger checks duplicates, clock types, future effective
    # prefixes and resource limits on a detached copy before writable access.
    proposed = GenomeLedger.from_snapshot(aquarium.genome.snapshot())
    proposed.append(event, proposed.revision)
    return event


def _parser():
    parser = argparse.ArgumentParser(
        description="Inspect or append one synthetic GENESIS acquisition to an existing Aquarium",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    packaged = files("mojum").joinpath("data")
    for command in ("status", "acquire"):
        child = commands.add_parser(command)
        child.add_argument("--db", type=Path, required=True,
                           help="existing PRIVATE local Aquarium journal")
        child.add_argument("--genome", type=Path, default=packaged.joinpath("genesis_seed.json"))
        child.add_argument("--graph", type=Path, default=packaged.joinpath("first_loop.json"))
        child.add_argument("--room-file", type=Path,
                           help="authored room pinned when the journal was created")
        child.add_argument("--expected-head", required=command == "acquire",
                           help="independent journal head; mandatory for acquisition")
        child.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
        child.add_argument("--motor-mode", choices=MOTOR_MODES, default="scripted")
        child.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                           default="placeholder")
        child.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"),
                           default="none")
        child.add_argument("--sensory-scene", choices=("default", "empty"), default="default",
                           help="same explicit scene used when the journal was created")
        child.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none")
        child.add_argument("--pulse-sum", choices=("auto", "ordered", "compensated"), default="auto")
        child.add_argument("--dt-ns", type=int, default=10_000_000,
                           help="virtual timestep pinned in the existing journal")
        if command == "acquire":
            child.add_argument("--event-id", required=True, help="unique acquisition identifier")
            child.add_argument("--gene", choices=tuple(GENES), required=True)
            child.add_argument("--delta", type=int, required=True,
                               help="integer delta in the existing gene's units")
            child.add_argument("--source", required=True, help="caller-declared provenance, not authentication")
            child.add_argument("--evidence", required=True, help="caller-declared synthetic evidence")
            child.add_argument("--valid-from-tick", type=int,
                               help="current or future tick; defaults to current tick")
            child.add_argument("--observed-at-ns", type=int,
                               help="observation time no later than current virtual time")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.expected_head is not None and (
                len(args.expected_head) != 64
                or any(char not in "0123456789abcdef" for char in args.expected_head)):
            raise ValueError("--expected-head must be a lowercase SHA-256 digest")
        if args.command == "acquire" and args.motor_mode not in ("scripted", "developmental_fixture"):
            raise ValueError("local-cell compatibility journals do not allow acquisitions")
        seed = _load_seed(args.genome)
        graph = load_graph(args.graph)
        room, start = load_cli_room(args.room_file, args.motor_mode)
        options = {
            "agent_id": seed["genome_id"], "room": room, "start": start,
            "neural_mode": args.neural_mode, "motor_mode": args.motor_mode,
            "brain_mode": args.brain_mode, "sensory_mode": args.sensory_mode,
            "sensory_scene": _scene(args.sensory_scene, args.sensory_mode, room),
            "temporal_mode": args.temporal_mode, "pulse_sum": args.pulse_sum,
            "dt_ns": args.dt_ns,
        }
        with Aquarium(args.db, seed, graph, read_only=True,
                      external_expected_head=args.expected_head, **options) as aquarium:
            if args.command == "status":
                result = _receipt(aquarium, args.command)
            else:
                event = _prepare_acquisition(aquarium, args)
                anchor = aquarium.checkpoint()["journal_head"]
        if args.command == "acquire":
            # No retry/rebase on a changed head. The replay anchor protects
            # the reopen, and Aquarium.acquire's CAS protects the append.
            with Aquarium(args.db, seed, graph, external_expected_head=anchor,
                          **options) as aquarium:
                aquarium.acquire(event)
                result = _receipt(aquarium, args.command, previous_head=anchor)
    except (ValueError, OSError, sqlite3.Error, RecursionError) as exc:
        parser.error(str(exc))
    # A successful append precedes this receipt. ASCII escapes retain Unicode
    # identities even on redirected legacy consoles without leaking contents.
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return result


if __name__ == "__main__":
    main()
