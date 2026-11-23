"""Bounded PUBLIC_SYNTHETIC point-room movement coverage witness (offline only).

This module replays commands through the canonical deterministic world-room fixture.
It neither predicts accessible free space nor authenticates a private journal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

from .world_room import Room, advance, initial_state

INPUT_SCHEMA = "mojum.world-coverage-input/1"
OUTPUT_SCHEMA = "mojum.world-coverage-witness/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_BYTES = 524_288
MAX_STEPS = 4096
MAX_OBSTACLES = 128
MAX_ROOM_M = 1000.0
MAX_GRID_AXIS = 256
MAX_GRID_CELLS = 65_536


class CoverageError(ValueError):
    """An invalid input or unsupported witness request; nothing is replayed."""


def _exact_keys(value: object, expected: tuple[str, ...], name: str) -> dict:
    if type(value) is not dict:
        raise CoverageError(f"invalid {name} mapping")
    # Validate native keys before set/equality: arbitrary str subclasses can run code.
    if any(type(key) is not str for key in value):
        raise CoverageError(f"invalid {name} keys")
    if set(value) != set(expected):
        raise CoverageError(f"invalid {name} fields")
    return value


def _number(value: object, name: str, *, positive: bool = False) -> int | float:
    if type(value) not in (int, float):
        raise CoverageError(f"invalid {name} number")
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        finite = False
    if not finite or abs(value) > MAX_ROOM_M or (positive and value <= 0):
        raise CoverageError(f"invalid {name} range")
    return value


def _pair(value: object, name: str) -> tuple[int | float, int | float]:
    if type(value) is not list or len(value) != 2:
        raise CoverageError(f"invalid {name} pair")
    return _number(value[0], name), _number(value[1], name)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _cell(x: float, y: float, cell_size: float, cols: int, rows: int) -> tuple[int, int]:
    return (min(cols - 1, int(x / cell_size)),
            min(rows - 1, int(y / cell_size)))


def witness(request: object) -> dict:
    """Replay explicitly supplied public fixture commands and return bounded facts.

    The input must be an exact built-in dict with INPUT_SCHEMA, PUBLIC_SYNTHETIC,
    room, start, cell_size_m and steps. The report includes a deterministic
    trace hash, not an authenticated signature or proof of environment reachability.
    """
    data = _exact_keys(request, ("schema", "classification", "room", "start",
                                 "cell_size_m", "steps"), "request")
    if type(data["schema"]) is not str or data["schema"] != INPUT_SCHEMA:
        raise CoverageError("wrong input schema")
    if type(data["classification"]) is not str or data["classification"] != CLASSIFICATION:
        raise CoverageError("only PUBLIC_SYNTHETIC fixtures admitted")
    raw_room = _exact_keys(data["room"], ("width", "height", "obstacles"), "room")
    width = _number(raw_room["width"], "width", positive=True)
    height = _number(raw_room["height"], "height", positive=True)
    raw_obstacles = raw_room["obstacles"]
    if type(raw_obstacles) is not list or len(raw_obstacles) > MAX_OBSTACLES:
        raise CoverageError("invalid obstacle collection")
    obstacles = []
    for item in raw_obstacles:
        if type(item) is not list or len(item) != 4:
            raise CoverageError("invalid rectangle")
        obstacles.append(tuple(_number(v, "rectangle") for v in item))
    start_x, start_y = _pair(data["start"], "start")
    cell_size = _number(data["cell_size_m"], "cell_size_m", positive=True)
    if cell_size < width / MAX_GRID_AXIS or cell_size < height / MAX_GRID_AXIS:
        raise CoverageError("grid exceeds budget")
    cols = math.ceil(width / cell_size)
    rows = math.ceil(height / cell_size)
    if (cols > MAX_GRID_AXIS or rows > MAX_GRID_AXIS or
            cols * rows > MAX_GRID_CELLS):
        raise CoverageError("grid exceeds budget")
    raw_steps = data["steps"]
    if type(raw_steps) is not list or len(raw_steps) > MAX_STEPS:
        raise CoverageError("steps exceed budget")
    # All inputs validated before any replay, including expensive room allocation.
    steps = []
    for step in raw_steps:
        record = _exact_keys(step, ("dx", "dy"), "step")
        dx, dy = _number(record["dx"], "dx"), _number(record["dy"], "dy")
        if math.hypot(dx, dy) > MAX_ROOM_M:
            raise CoverageError("movement exceeds fixture budget")
        steps.append((dx, dy))
    try:
        room = Room(width, height, tuple(obstacles))
        state = initial_state(room, start_x, start_y)
    except (ValueError, OverflowError, TypeError) as exc:
        raise CoverageError("invalid room or starting position") from exc

    first_revision = state.revision
    visited = {_cell(state.x, state.y, cell_size, cols, rows)}
    blocked = moved = no_progress = 0
    distance = 0.0
    trace = hashlib.sha256()
    trace.update(_canonical(["mojum.world-coverage-trace/1", data]))
    trace.update(b"\n")
    for tick, (dx, dy) in enumerate(steps, 1):
        previous = state
        try:
            state, observation = advance(room, state, tick=tick, dx=dx,
                                         dy=dy, expected_revision=state.revision)
        except (ValueError, OverflowError, TypeError) as exc:
            raise CoverageError("unreplayable movement") from exc
        was_blocked = observation["blocked"]
        if was_blocked:
            blocked += 1
        elif (previous.x, previous.y) == (state.x, state.y):
            no_progress += 1
        else:
            moved += 1
        distance += math.hypot(state.x - previous.x, state.y - previous.y)
        visited.add(_cell(state.x, state.y, cell_size, cols, rows))
        trace.update(_canonical([tick, dx, dy, was_blocked,
                                 state.x, state.y, state.revision]))
        trace.update(b"\n")
    if not math.isfinite(distance):
        raise CoverageError("nonfinite travel distance")
    return {
        "schema": OUTPUT_SCHEMA,
        "classification": CLASSIFICATION,
        "world_provenance": "deterministic-point-room-fixture/v1",
        "start_revision": first_revision,
        "final_revision": state.revision,
        "trace_sha256": trace.hexdigest(),
        "ticks": len(steps),
        "blocked_steps": blocked,
        "moving_steps": moved,
        "no_progress_steps": no_progress,
        "distance_m": distance,
        "grid_columns": cols,
        "grid_rows": rows,
        "nominal_grid_cells": cols * rows,
        "visited_cell_count": len(visited),
        "visited_cells": [list(p) for p in sorted(visited)],
        "final_position": [state.x, state.y],
        "physical_contact": False,
    }


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    output = {}
    for key, value in pairs:
        if key in output:
            raise CoverageError("duplicate JSON keys")
        output[key] = value
    return output


def _no_constant(_: str) -> None:
    raise CoverageError("invalid nonfinite JSON constant")


def _check_json_depth(raw: bytes) -> None:
    # Guard deeply nested JSON *before* invoking the recursive CPython parser.
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:  # backslash
                escaped = True
            elif char == 34:  # quote
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):  # [ {
            depth += 1
            if depth > 16:
                raise CoverageError("JSON nesting exceeds budget")
        elif char in (93, 125):  # ] }
            depth -= 1
            if depth < 0:
                raise CoverageError("invalid JSON structure")


def decode(raw: bytes) -> dict:
    """Strict bounded UTF-8 JSON boundary (duplicate keys forbidden)."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise CoverageError("input exceeds byte budget")
    _check_json_depth(raw)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError,
            OverflowError) as exc:
        raise CoverageError("invalid JSON input") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline PUBLIC_SYNTHETIC room coverage witness")
    parser.add_argument("json_input", type=Path, help="bounded PUBLIC_SYNTHETIC JSON fixture")
    args = parser.parse_args(argv)
    try:
        # Read one extra byte to fail closed without buffering arbitrary sized files.
        with args.json_input.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        result = witness(decode(raw))
    except (OSError, CoverageError) as exc:
        print(f"world-coverage: {type(exc).__name__}", file=sys.stderr)
        return 2
    sys.stdout.write(_canonical(result).decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
