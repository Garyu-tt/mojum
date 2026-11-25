"""Inspect or advance an existing Aquarium's journaled synthetic development."""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
from pathlib import Path
import sqlite3

from .aquarium import Aquarium, AquariumError, MOTOR_MODES, _safe_event_text, load_cli_room
from .genesis import validate_seed
from .graph import load_graph, validate as validate_graph
from .noosphere import NoosphereError


SCHEMA = "mojum.aquarium.development-receipt/1"
MAX_GENOME_BYTES = 256 * 1024


def _expected_head(value):
    if value is not None and (
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
        raise AquariumError("--expected-head must be exactly 64 lowercase hexadecimal characters")
    return value


def _existing_journal(path):
    if path.is_symlink() or not path.is_file():
        raise AquariumError("--db requires an existing regular committed Aquarium journal")


def _config_file(path, label):
    # Packaged Traversable resources also work from zip imports. Explicit
    # filesystem inputs must not block on a FIFO or follow a file symlink.
    if isinstance(path, Path) and (path.is_symlink() or not path.is_file()):
        raise AquariumError(f"{label} requires an existing regular file")
    return path


def _unique_seed_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AquariumError("--genome contains duplicate JSON keys")
        result[key] = value
    return result


def _load_seed(path):
    try:
        with _config_file(path, "--genome").open("rb") as stream:
            data = stream.read(MAX_GENOME_BYTES + 1)
        if len(data) > MAX_GENOME_BYTES:
            raise AquariumError("--genome exceeds the 256 KiB input limit")
        seed = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_seed_keys)
        return validate_seed(seed)
    except AquariumError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise AquariumError("--genome requires valid bounded UTF-8 GENESIS JSON") from exc


def _receipt(aquarium, operation):
    count = aquarium.stored_events
    checkpoint = aquarium.checkpoint()  # Reject a stale adopted head last.
    return {
        "schema": SCHEMA, "operation": operation,
        "tick": checkpoint["tick"], "stored_events": count,
        "development_months": checkpoint["development_months"],
        "journal_head": checkpoint["journal_head"],
        "manifest_sha256": checkpoint["manifest_sha256"],
    }


def _operate(args):
    expected_head = _expected_head(args.expected_head)
    if args.command == "advance":
        if not 1 <= args.months <= 12:
            raise AquariumError("--months must be within 1..12")
        _safe_event_text(args.reason, "development reason")
        try:
            args.reason.encode("utf-8")
        except UnicodeError as exc:
            raise AquariumError("development reason must be valid UTF-8") from exc
        if args.motor_mode not in ("scripted", "developmental_fixture"):
            raise AquariumError("development advances require scripted or developmental_fixture mode")

    _existing_journal(args.db)
    seed = _load_seed(args.genome)
    graph = load_graph(_config_file(args.graph, "--graph"))
    validate_graph(graph)
    room, start = load_cli_room(args.room_file, args.motor_mode)
    options = {
        "agent_id": seed["genome_id"], "room": room, "start": start,
        "neural_mode": args.neural_mode, "motor_mode": args.motor_mode,
        "brain_mode": args.brain_mode, "sensory_mode": args.sensory_mode,
        "temporal_mode": args.temporal_mode, "pulse_sum": args.pulse_sum,
    }

    # A regular file is not necessarily an organism. Replay read-only before
    # any writable SQLite open, including for valid zero-tick journals.
    with Aquarium(args.db, seed, graph, read_only=True,
                  external_expected_head=expected_head, **options) as existing:
        before = _receipt(existing, "status")
        if args.command == "status":
            return before
        if before["development_months"] + args.months > 192:
            raise AquariumError("development maximum age is 16 years")
        if before["stored_events"] >= existing.journal_event_budget:
            raise AquariumError("journal event budget reached")

    # The constructor binds this reopen to the verified head. The existing
    # API's compare-and-append also fences a writer arriving after the reopen.
    _existing_journal(args.db)
    with Aquarium(args.db, seed, graph,
                  external_expected_head=before["journal_head"], **options) as aquarium:
        aquarium.advance_development(args.months, reason=args.reason)
        return _receipt(aquarium, "advance")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect or advance synthetic development in an existing Aquarium journal")
    packaged = files("mojum").joinpath("data")
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("status", "advance"):
        command = commands.add_parser(operation)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--expected-head", required=operation == "advance",
                             metavar="SHA256", help="require this committed journal head")
        command.add_argument("--genome", type=Path, default=packaged.joinpath("genesis_seed.json"))
        command.add_argument("--graph", type=Path, default=packaged.joinpath("first_loop.json"))
        command.add_argument("--room-file", type=Path)
        command.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
        command.add_argument("--motor-mode", choices=MOTOR_MODES, default="scripted")
        command.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                             default="placeholder")
        command.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"), default="none")
        command.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none")
        command.add_argument("--pulse-sum", choices=("auto", "ordered", "compensated"), default="auto")
        if operation == "advance":
            command.add_argument("--months", type=int, required=True)
            command.add_argument("--reason", default="explicit simulation advancement")
    args = parser.parse_args(argv)
    try:
        receipt = _operate(args)
    except (ValueError, NoosphereError, sqlite3.OperationalError, OSError) as exc:
        storage_error = exc.__cause__ if isinstance(exc, NoosphereError) else exc
        if isinstance(storage_error, sqlite3.OperationalError):
            parser.exit(1, f"{parser.prog}: cannot access SQLite journal\n")
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
