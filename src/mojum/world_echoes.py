"""Bounded first-order geometric echo observations for synthetic point rooms.

This is an image-source geometry fixture, not a waveform or room-acoustics solver.
"""
from __future__ import annotations

from hashlib import sha256
from math import hypot, isfinite
import json

from .world_room import Room, RoomState, _revision, _segment_hits_rect, _valid

EVENT_SCHEMA = "mojum.world.synthetic-sound/1"
OBSERVATION_SCHEMA = "mojum.world.echo-observation/1"
MAX_EVENTS = 16
MAX_AGE_TICKS = 100_000
MAX_LEVEL_MILLI = 1_000_000
MAX_RANGE_MM = 100_000
MAX_COORDINATE_MM = 1_000_000_000_000


class EchoError(ValueError):
    """Rejected synthetic sound event or invalid room revision."""


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise EchoError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _mm(value, name):
    scaled = value * 1000
    if not isfinite(scaled) or abs(scaled) > MAX_COORDINATE_MM:
        raise EchoError(f"{name} exceeds the supported millimetre range")
    return round(scaled)


def _admit_room(room, state, expected_revision):
    if type(room) is not Room or type(state) is not RoomState:
        raise EchoError("room/state must be canonical Room and RoomState")
    try:
        checked = Room(room.width, room.height, room.obstacles)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EchoError("invalid room geometry") from exc
    if (type(state.tick) is not int or state.tick < 0
            or not _finite(state.x) or not _finite(state.y)
            or not _valid(checked, state.x, state.y)):
        raise EchoError("invalid listener state")
    if (type(state.revision) is not str
            or type(expected_revision) is not str
            or state.revision != expected_revision
            or state.revision != _revision(checked, state.tick, state.x, state.y)):
        raise EchoError("stale or corrupted room revision")
    return checked


def _admit_event(event, room, tick, max_age):
    required = {"schema", "event_id", "source_tick", "x", "y", "source_level_milli"}
    if type(event) is not dict or len(event) != len(required):
        raise EchoError("event must be a plain object with exact keys")
    if any(type(key) is not str for key in event) or set(event) != required:
        raise EchoError("event must be a plain object with exact keys")
    if type(event["schema"]) is not str or event["schema"] != EVENT_SCHEMA:
        raise EchoError("unsupported event schema")
    event_id = event["event_id"]
    if (type(event_id) is not str or not event_id or len(event_id) > 80):
        raise EchoError("event_id must be a nonempty string of at most 80 characters")
    try:
        event_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EchoError("event_id must be valid UTF-8") from exc
    source_tick = _integer(event["source_tick"], "source_tick", 0, 2**63 - 1)
    if source_tick > tick or tick - source_tick > max_age:
        raise EchoError("event is future-dated or expired")
    x, y = event["x"], event["y"]
    if not _finite(x) or not _finite(y) or not _valid(room, x, y):
        raise EchoError("source must be finite and inside free room space")
    level = _integer(event["source_level_milli"], "source_level_milli", 0, MAX_LEVEL_MILLI)
    return {"event_id": event_id, "source_tick": source_tick,
            "x": x, "y": y, "source_level_milli": level}


def _first_order_reflection(room, sx, sy, lx, ly, wall):
    """Return one image-source bounce, or None when geometry is degenerate."""
    if wall == "left":
        image, boundary = (-sx, sy), ("x", 0.0)
    elif wall == "right":
        image, boundary = (2 * room.width - sx, sy), ("x", room.width)
    elif wall == "bottom":
        image, boundary = (sx, -sy), ("y", 0.0)
    else:
        image, boundary = (sx, 2 * room.height - sy), ("y", room.height)

    axis, edge = boundary
    denominator = image[0] - lx if axis == "x" else image[1] - ly
    if denominator == 0:
        return None
    t = (edge - (lx if axis == "x" else ly)) / denominator
    if not 0 < t < 1:
        return None
    bx, by = lx + t * (image[0] - lx), ly + t * (image[1] - ly)
    along = by if axis == "x" else bx
    limit = room.height if axis == "x" else room.width
    if not 0 <= along <= limit:
        return None
    # A bounce point inside/against a solid obstacle is not a valid wall echo.
    if any(a <= bx <= a + w and b <= by <= b + h
           for a, b, w, h in room.obstacles):
        return None
    if any(_segment_hits_rect(sx, sy, bx, by, rect)
           or _segment_hits_rect(bx, by, lx, ly, rect)
           for rect in room.obstacles):
        return None
    return bx, by


def observe_first_order_echoes(room: Room, state: RoomState, events, *,
                               expected_revision: str,
                               max_range_mm: int = 30_000,
                               max_age_ticks: int = 1_000) -> dict:
    """Return deterministic first-order wall echoes for bounded synthetic events.

    Each event yields at most four specular image-source paths, one per outer
    room wall. Rectangular obstacles are treated as opaque blockers, not
    reflectors. The integer-millimetre path length and inverse-distance level
    are fixture metrics only; no waveform, phase, material, diffraction,
    reverberation-time or calibrated SPL is computed.
    """
    room = _admit_room(room, state, expected_revision)
    max_range_mm = _integer(max_range_mm, "max_range_mm", 1, MAX_RANGE_MM)
    max_age_ticks = _integer(max_age_ticks, "max_age_ticks", 0, MAX_AGE_TICKS)
    if type(events) not in (list, tuple) or len(events) > MAX_EVENTS:
        raise EchoError(f"events must contain at most {MAX_EVENTS} entries")

    admitted, seen = [], set()
    for raw in events:
        event = _admit_event(raw, room, state.tick, max_age_ticks)
        if event["event_id"] in seen:
            raise EchoError("duplicate event ID")
        seen.add(event["event_id"])
        admitted.append(event)

    # Check all admitted coordinates before doing geometry or integer conversion.
    lx, ly = _mm(state.x, "listener.x"), _mm(state.y, "listener.y")
    output = []
    for event in admitted:
        sx, sy = event["x"], event["y"]
        _mm(sx, "source.x")
        _mm(sy, "source.y")
        echoes = []
        for wall in ("left", "right", "bottom", "top"):
            bounce = _first_order_reflection(room, sx, sy, state.x, state.y, wall)
            if bounce is None:
                continue
            bx, by = bounce
            length = (hypot(sx - bx, sy - by)
                      + hypot(state.x - bx, state.y - by))
            if length * 1000 > max_range_mm + 0.5:
                continue
            path_mm = _mm(length, "echo.path_length")
            if path_mm > max_range_mm:
                continue
            received = event["source_level_milli"] * 1000 // (1000 + path_mm)
            echoes.append({
                "wall": wall,
                "bounce_mm": [_mm(bx, "bounce.x"), _mm(by, "bounce.y")],
                "path_length_mm": path_mm,
                "received_level_milli": received,
            })
        echoes.sort(key=lambda item: (item["path_length_mm"], item["wall"]))
        output.append({"event_id": event["event_id"],
                       "age_ticks": state.tick - event["source_tick"],
                       "echoes": echoes})

    output.sort(key=lambda item: item["event_id"])
    result = {
        "schema": OBSERVATION_SCHEMA,
        "tick": state.tick,
        "room_revision": state.revision,
        "listener_position_mm": [lx, ly],
        "events": output,
        "source_classification": "PUBLIC_SYNTHETIC_FIRST_ORDER_GEOMETRIC_ECHOES_NOT_AUDIO",
    }
    wire = json.dumps(result, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    result["observation_sha256"] = sha256(wire).hexdigest()
    return result


__all__ = ["EchoError", "EVENT_SCHEMA", "OBSERVATION_SCHEMA",
           "observe_first_order_echoes"]
