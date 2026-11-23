"""Bounded offline attestation of synthetic room + object interaction traces.

The canonical fixture remains authoritative. Hashes detect accidental corruption,
not adversarial re-signing; no network, journal writes or physical contact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from mojum.world_room import Room, initial_state
from mojum.world_objects import (
    ObjectState, advance_objects, initial_objects, snapshot_objects,
)

SCHEMA = "mojum.world.object-trace/1"
ATTESTATION_SCHEMA = "mojum.world.object-trace-attestation/1"
AUDIENCE = "PUBLIC_SYNTHETIC"
MAX_STEPS = 128
MAX_OBSTACLES = 128
MAX_OBJECTS = 16
MAX_COORD = 10_000
MAX_BYTES = 262_144
_HEX = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_COMMAND_KEYS = {"dx", "dy", "action", "object_id", "place"}
_TOP_KEYS = {
    "schema", "audience", "room", "start", "objects", "steps",
    "final_room_revision", "final_objects_revision", "trace_sha256",
}
_STEP_KEYS = {"command", "room_event", "object_event", "room_state", "objects_state"}


def _fail(message: str):
    raise ValueError(message)


def _number(value, limit=MAX_COORD):
    if type(value) not in (float, int):
        _fail("expected native JSON number, not bool or object")
    try:
        if not math.isfinite(value) or abs(value) > limit:
            _fail("non-finite or out-of-range number")
    except (OverflowError, TypeError):
        _fail("out-of-range number")
    return value


def _plain_json(value):
    """Bound work before serializing anything supplied by a caller."""
    stack = [(value, 0)]
    nodes = 0
    budget = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 40_000 or depth > 12:
            _fail("JSON nesting or node budget exceeded")
        t = type(item)
        if t is dict:
            if len(item) > 32:
                _fail("JSON object too large")
            for key, child in item.items():
                if type(key) is not str:
                    _fail("JSON object key must be string")
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif t is list:
            if len(item) > 2048:
                _fail("JSON array too large")
            stack.extend((child, depth + 1) for child in item)
        elif t is str:
            if len(item) > 2048:
                _fail("JSON string too long")
            try:
                budget += len(item.encode("utf-8"))
            except UnicodeError as exc:
                raise ValueError("invalid Unicode") from exc
            if budget > MAX_BYTES:
                _fail("JSON text budget exceeded")
        elif t in (int, float):
            _number(item, 10**16)
        elif t is bool or item is None:
            pass
        else:
            _fail("non-JSON value")


def _encode(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _room(room):
    if type(room) is not Room:
        _fail("expected canonical Room")
    w = _number(room.width)
    h = _number(room.height)
    if w <= 0 or h <= 0 or type(room.obstacles) is not tuple:
        _fail("invalid room geometry")
    if len(room.obstacles) > MAX_OBSTACLES:
        _fail("room obstacle budget exceeded")
    for rect in room.obstacles:
        if type(rect) is not tuple or len(rect) != 4:
            _fail("invalid rectangle")
        for value in rect:
            _number(value)
    try:
        return Room(w, h, room.obstacles)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid room geometry") from exc


def _command(value):
    if type(value) is not dict or set(value) != _COMMAND_KEYS:
        _fail("invalid command keys")
    dx = _number(value["dx"], 1000)
    dy = _number(value["dy"], 1000)
    action = value["action"]
    ident = value["object_id"]
    place = value["place"]
    if type(action) is not str or action not in ("wait", "pick", "place"):
        _fail("invalid action")
    if ident is not None and (type(ident) is not str or len(ident) > 32):
        _fail("invalid object id")
    if place is not None:
        if type(place) is not list or len(place) != 2:
            _fail("invalid placement")
        for coordinate in place:
            _number(coordinate)
    if action == "wait" and (ident is not None or place is not None):
        _fail("wait cannot carry target data")
    if action == "pick" and (ident is None or place is not None):
        _fail("pick requires an id and no placement")
    if action == "place" and (ident is None or place is None):
        _fail("place requires an id and placement")
    return {"dx": dx, "dy": dy, "action": action, "object_id": ident,
            "place": None if place is None else list(place)}


def record_trace(room: Room, x, y, fixtures, commands):
    """Record only bounded PUBLIC_SYNTHETIC fixture steps via canonical code."""
    room = _room(room)
    x, y = _number(x), _number(y)
    if type(fixtures) not in (tuple, list) or len(fixtures) > MAX_OBJECTS:
        _fail("object fixture budget exceeded")
    if type(commands) not in (tuple, list) or len(commands) > MAX_STEPS:
        _fail("step budget exceeded")
    # Validate the full command input before executing any step.
    checked = [_command(c) for c in commands]
    if any(type(item) not in (tuple, list) or len(item) != 3 for item in fixtures):
        _fail("invalid fixture entries")
    state = initial_state(room, x, y)
    objects = initial_objects(room, state, fixtures)
    initial_items = [list(item) for item in objects.items]
    steps = []
    for cmd in checked:
        state, objects, event = advance_objects(
            room, state, objects, tick=state.tick + 1,
            dx=cmd["dx"], dy=cmd["dy"], expected_room_revision=state.revision,
            expected_objects_revision=objects.revision,
            action=cmd["action"], object_id=cmd["object_id"],
            place=cmd["place"],
        )
        # Independently derive the canonical room frame from the transition.
        # The object event is emitted by the authoritative joint state machine.
        room_event = {
            "tick": state.tick, "revision": state.revision,
            "position": [state.x, state.y],
            "blocked": bool(event["move_blocked"]),
            "provenance": "deterministic-point-room-fixture/v1",
            "physical_contact": False,
        }
        steps.append({
            "command": cmd,
            "room_event": room_event,
            "object_event": event,
            "room_state": {
                "tick": state.tick, "x": state.x, "y": state.y,
                "revision": state.revision,
            },
            "objects_state": snapshot_objects(room, objects),
        })
    doc = {
        "schema": SCHEMA, "audience": AUDIENCE,
        "room": {
            "width": room.width, "height": room.height,
            "obstacles": [list(r) for r in room.obstacles],
        },
        "start": {"x": x, "y": y}, "objects": initial_items,
        "steps": steps,
        "final_room_revision": state.revision,
        "final_objects_revision": objects.revision,
    }
    doc["trace_sha256"] = _digest(doc)
    if len(_encode(doc)) > MAX_BYTES:
        _fail("trace byte budget exceeded")
    return doc


def attest_trace(document):
    """Re-execute every step; reject edited events even with a new checksum."""
    _plain_json(document)
    if type(document) is not dict or set(document) != _TOP_KEYS:
        _fail("invalid trace envelope")
    if document["schema"] != SCHEMA or document["audience"] != AUDIENCE:
        _fail("unsupported schema or audience")
    checksum = document["trace_sha256"]
    if type(checksum) is not str or _HEX.fullmatch(checksum) is None:
        _fail("invalid trace digest")
    if len(_encode(document)) > MAX_BYTES:
        _fail("trace byte budget exceeded")
    without_checksum = {k: v for k, v in document.items() if k != "trace_sha256"}
    if _digest(without_checksum) != checksum:
        _fail("trace checksum mismatch")
    spec = document["room"]
    start = document["start"]
    initial_items = document["objects"]
    records = document["steps"]
    if type(spec) is not dict or set(spec) != {"width", "height", "obstacles"}:
        _fail("invalid room manifest")
    if type(start) is not dict or set(start) != {"x", "y"}:
        _fail("invalid starting coordinates")
    rects = spec["obstacles"]
    if type(rects) is not list or len(rects) > MAX_OBSTACLES:
        _fail("invalid obstacle list")
    if any(type(r) is not list or len(r) != 4 for r in rects):
        _fail("invalid obstacle rectangle")
    if type(initial_items) is not list or len(initial_items) > MAX_OBJECTS:
        _fail("invalid starting objects")
    if any(type(o) is not list or len(o) != 3 for o in initial_items):
        _fail("invalid starting object")
    if type(records) is not list or len(records) > MAX_STEPS:
        _fail("invalid step count")
    commands = []
    for record in records:
        if type(record) is not dict or set(record) != _STEP_KEYS:
            _fail("invalid step envelope")
        commands.append(record["command"])
    try:
        room = Room(
            spec["width"], spec["height"],
            tuple(tuple(rect) for rect in rects),
        )
        rebuilt = record_trace(
            room, start["x"], start["y"],
            initial_items, commands,
        )
    except (TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid trace fixture or transition") from exc
    # Comparing canonical bytes catches numeric type spoofing (True/1, 1/1.0)
    # and any fabricated/reordered frame or unsupported extra key.
    if _encode(document) != _encode(rebuilt):
        _fail("trace disagrees with canonical object-room replay")
    return {
        "schema": ATTESTATION_SCHEMA, "audience": AUDIENCE,
        "verified_steps": len(records),
        "trace_sha256": checksum,
        "final_room_revision": rebuilt["final_room_revision"],
        "final_objects_revision": rebuilt["final_objects_revision"],
        "physical_contact": False,
        "authority": "offline_synthetic_fixture_only",
    }


def decode_trace_json(raw):
    """Strict duplicate-key-aware JSON decoder; no implicit network or files."""
    if type(raw) not in (bytes, str) or len(raw) > MAX_BYTES:
        _fail("trace input byte budget exceeded")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                _fail("duplicate JSON key")
            out[key] = value
        return out
    def constant(_):
        _fail("nonfinite JSON constant")
    try:
        doc = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError, TypeError, OverflowError) as exc:
        raise ValueError("invalid trace JSON") from exc
    _plain_json(doc)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="explicit PUBLIC_SYNTHETIC JSON trace")
    args = parser.parse_args(argv)
    try:
        with args.trace.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        result = attest_trace(decode_trace_json(raw))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"trace audit rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
