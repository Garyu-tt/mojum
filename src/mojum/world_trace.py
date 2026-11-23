"""Bounded offline, deterministic point-room trace recording and verification.

This attests consistency with the *synthetic* world_room solver, not identity,
physical contact, source authenticity, or a private Aquarium SQLite journal.
No network, filesystem writes, wall-clock access, or optional dependencies.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

from .world_room import Room, RoomState, advance, initial_state

SCHEMA = "mojum.world.point-room-trace/1"
REPORT_SCHEMA = "mojum.world.point-room-trace-report/1"
MAX_STEPS = 1024
MAX_INPUT_BYTES = 512 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FRAME_FIELDS = frozenset(("tick", "dx", "dy", "expected_revision", "observation"))
_OBSERVATION_FIELDS = frozenset((
    "tick", "revision", "position", "blocked", "provenance", "physical_contact"
))


class TraceError(ValueError):
    """Malformed, stale, or internally inconsistent synthetic trace."""


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _mapping(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise TraceError(f"{label} must contain exactly {', '.join(sorted(fields))}")


def _pair(value, label):
    if type(value) is not list or len(value) != 2 or not all(map(_number, value)):
        raise TraceError(f"{label} must contain two finite JSON numbers")
    return value


def _revision(value, label):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise TraceError(f"{label} must be a lowercase SHA-256 hex revision")


def _json_equal(left, right):
    """Compare exact JSON scalar types, unlike Python where True == 1."""
    return json.dumps(left, allow_nan=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def make_trace(room: Room, start: tuple, actions) -> dict:
    """Record authoritative synthetic outcomes from deterministic actions.

    ``actions`` is a bounded list/tuple of (dx, dy) pairs. The resulting
    object has no external integrity anchor; callers must not treat its
    unkeyed revisions as cryptographic provenance.
    """
    if not isinstance(room, Room) or type(start) not in (tuple, list):
        raise TraceError("room and start must be explicit")
    _pair(list(start), "start")
    if type(actions) not in (tuple, list) or len(actions) > MAX_STEPS:
        raise TraceError("actions exceed bounded list budget")
    try:
        state = initial_state(room, start[0], start[1])
    except ValueError as exc:
        raise TraceError("invalid starting position") from exc
    frames = []
    for i, action in enumerate(actions, start=1):
        if type(action) not in (list, tuple):
            raise TraceError(f"action {i} must be a pair")
        dx, dy = _pair(list(action), f"action {i}")
        previous_revision = state.revision
        try:
            state, observation = advance(
                room, state, tick=i, dx=dx, dy=dy,
                expected_revision=previous_revision,
            )
        except ValueError as exc:
            raise TraceError(f"action {i} rejected by world solver") from exc
        frames.append({
            "tick": i, "dx": dx, "dy": dy,
            "expected_revision": previous_revision,
            "observation": observation,
        })
    document = {
        "schema": SCHEMA,
        "room": {"width": room.width, "height": room.height,
                 "obstacles": [list(rect) for rect in room.obstacles]},
        "start": list(start),
        "frames": frames,
        "final_revision": state.revision,
    }
    if len(json.dumps(document, allow_nan=False, separators=(",", ":")).encode()) > MAX_INPUT_BYTES:
        raise TraceError("trace exceeds byte limit")
    return document


def verify_trace(document: dict) -> RoomState:
    """Re-simulate all steps and refuse stale/tampered observed frames.

    All fields are explicit, with no acceptance of unknown extensions or
    partial prefixes. Runtime geometry preserves JSON numeric types; it
    never silently converts parsed integer values to floats on load.
    """
    _mapping(document, frozenset((
        "schema", "room", "start", "frames", "final_revision"
    )), "trace")
    if type(document["schema"]) is not str or document["schema"] != SCHEMA:
        raise TraceError("unsupported trace schema")
    _mapping(document["room"], frozenset(("width", "height", "obstacles")), "room")
    room_wire = document["room"]
    if not _number(room_wire["width"]) or not _number(room_wire["height"]):
        raise TraceError("invalid room dimensions")
    obstacles = room_wire["obstacles"]
    if type(obstacles) is not list or len(obstacles) > 4096:
        raise TraceError("invalid obstacle collection")
    for index, rect in enumerate(obstacles):
        if type(rect) is not list or len(rect) != 4 or not all(map(_number, rect)):
            raise TraceError(f"invalid obstacle {index}")
    _pair(document["start"], "start")
    _revision(document["final_revision"], "final_revision")
    frames = document["frames"]
    if type(frames) is not list or len(frames) > MAX_STEPS:
        raise TraceError("invalid frame count")
    try:
        room = Room(room_wire["width"], room_wire["height"], tuple(tuple(r) for r in obstacles))
        state = initial_state(room, *document["start"])
    except ValueError as exc:
        raise TraceError("invalid room or starting position") from exc
    for index, frame in enumerate(frames, start=1):
        _mapping(frame, _FRAME_FIELDS, f"frame {index}")
        if type(frame["tick"]) is not int or frame["tick"] != index:
            raise TraceError(f"frame {index} has invalid tick")
        if not _number(frame["dx"]) or not _number(frame["dy"]):
            raise TraceError(f"frame {index} has non-finite displacement")
        _revision(frame["expected_revision"], f"frame {index} previous revision")
        if frame["expected_revision"] != state.revision:
            raise TraceError(f"frame {index} has stale expected revision")
        _mapping(frame["observation"], _OBSERVATION_FIELDS, f"frame {index} observation")
        try:
            next_state, observed = advance(
                room, state, tick=index,
                dx=frame["dx"], dy=frame["dy"],
                expected_revision=frame["expected_revision"],
            )
        except ValueError as exc:
            raise TraceError(f"frame {index} rejected by world solver") from exc
        try:
            matches = _json_equal(frame["observation"], observed)
        except (ValueError, TypeError, OverflowError) as exc:
            raise TraceError(f"frame {index} malformed observation") from exc
        if not matches:
            raise TraceError(f"frame {index} observation differs from measured simulation")
        state = next_state
    if document["final_revision"] != state.revision:
        raise TraceError("final revision mismatch")
    return state


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TraceError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(token):
    raise TraceError("non-finite JSON constant")


def verify_trace_bytes(raw: bytes) -> RoomState:
    """Parse a size-bounded strict JSON document and verify every frame."""
    if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
        raise TraceError("trace must be UTF-8 bytes within limit")
    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except TraceError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise TraceError("invalid trace JSON") from exc
    return verify_trace(document)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only verification of offline synthetic world-room traces"
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        state = verify_trace_bytes(raw)
    except (OSError, TraceError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "valid": False, "error": str(exc)}))
        return 1
    print(json.dumps({"schema": REPORT_SCHEMA, "valid": True,
                      "ticks": state.tick, "final_revision": state.revision}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
