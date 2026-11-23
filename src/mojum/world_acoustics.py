"""Bounded synthetic point-room acoustic event propagation."""
from __future__ import annotations
from hashlib import sha256
from math import isfinite, isqrt
import json
from .world_room import Room, RoomState, _revision, _segment_hits_rect, _valid

EVENT_SCHEMA = "mojum.world.synthetic-sound/1"
OBSERVATION_SCHEMA = "mojum.world.acoustic-observation/1"
MAX_EVENTS = 32
MAX_RANGE_MM = 100_000
MAX_AGE_TICKS = 100_000
MAX_LEVEL_MILLI = 1_000_000

class AcousticError(ValueError):
    """Rejected synthetic event or stale point-room state."""

def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False

def _millimetres(value, name):
    """Convert a finite metre coordinate without leaking float overflow."""
    try:
        scaled = value * 1000
        if not isfinite(scaled):
            raise OverflowError
        return round(scaled)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AcousticError(f"{name} is outside the millimetre coordinate range") from exc


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise AcousticError(f"{name} must be an integer in [{low}, {high}]")
    return value

def _text(value, name, limit):
    if type(value) is not str or not value or len(value) > limit:
        raise AcousticError(f"{name} must be a nonempty string (<= {limit})")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AcousticError(f"{name} must be valid UTF-8") from exc
    return value

def _check_room_state(room, state, expected_revision):
    if type(room) is not Room or type(state) is not RoomState:
        raise AcousticError("room/state must be canonical Room and RoomState")
    try:
        checked = Room(room.width, room.height, room.obstacles)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AcousticError("invalid room geometry") from exc
    if (type(state.tick) is not int or state.tick < 0
            or not _finite(state.x) or not _finite(state.y)
            or not _valid(checked, state.x, state.y)):
        raise AcousticError("invalid listener state")
    if (type(state.revision) is not str or type(expected_revision) is not str
            or state.revision != expected_revision
            or state.revision != _revision(checked, state.tick, state.x, state.y)):
        raise AcousticError("stale or corrupted room revision")
    return checked

def _check_event(event, room, tick, max_age):
    required = {"schema", "event_id", "source_tick", "x", "y", "source_level_milli"}
    if type(event) is not dict:
        raise AcousticError("event must be a plain object with exact keys")
    # The schema is fixed-size: reject oversized mappings before iterating or
    # allocating a set containing caller-controlled keys.
    if len(event) != len(required):
        raise AcousticError("event must be a plain object with exact keys")
    if any(type(key) is not str for key in event) or set(event) != required:
        raise AcousticError("event must be a plain object with exact keys")
    if type(event["schema"]) is not str or event["schema"] != EVENT_SCHEMA:
        raise AcousticError("unsupported sound event schema")
    event_id = _text(event["event_id"], "event_id", 80)
    source_tick = _int(event["source_tick"], "source_tick", 0, 2**63 - 1)
    if source_tick > tick or tick - source_tick > max_age:
        raise AcousticError("sound event is future-dated or expired")
    if not _finite(event["x"]) or not _finite(event["y"]):
        raise AcousticError("source coordinates must be finite numbers")
    if not _valid(room, event["x"], event["y"]):
        raise AcousticError("sound source must be inside free room space")
    level = _int(event["source_level_milli"], "source_level_milli", 0, MAX_LEVEL_MILLI)
    return {"event_id": event_id, "source_tick": source_tick,
            "x": event["x"], "y": event["y"], "source_level_milli": level}

def sense_sound_events(room: Room, state: RoomState, events, *,
                       expected_revision: str, max_range_mm: int = 30_000,
                       threshold_milli: int = 10, max_age_ticks: int = 1_000) -> dict:
    """Observe caller-supplied synthetic events using deterministic geometry.

    Distance is rounded to integer millimetres. The fixture attenuation rule is
    source_level_milli * 1000 // (1000 + distance_mm). Any solid obstacle crossed
    by the source-listener segment fully occludes the event. These are simple
    geometric rules, not calibrated acoustics or sound-pressure measurements.
    """
    room = _check_room_state(room, state, expected_revision)
    max_range_mm = _int(max_range_mm, "max_range_mm", 1, MAX_RANGE_MM)
    threshold_milli = _int(threshold_milli, "threshold_milli", 0, MAX_LEVEL_MILLI)
    max_age_ticks = _int(max_age_ticks, "max_age_ticks", 0, MAX_AGE_TICKS)
    if type(events) not in (list, tuple) or len(events) > MAX_EVENTS:
        raise AcousticError(f"events must contain at most {MAX_EVENTS} entries")
    normalized, seen = [], set()
    for raw in events:
        event = _check_event(raw, room, state.tick, max_age_ticks)
        if event["event_id"] in seen:
            raise AcousticError("duplicate sound event ID")
        seen.add(event["event_id"])
        normalized.append(event)

    lx = _millimetres(state.x, "listener x")
    ly = _millimetres(state.y, "listener y")
    observed = []
    for event in normalized:
        dx = _millimetres(event["x"], "source x") - lx
        dy = _millimetres(event["y"], "source y") - ly
        distance = isqrt(dx * dx + dy * dy)
        occluded = any(_segment_hits_rect(state.x, state.y, event["x"], event["y"], r)
                       for r in room.obstacles)
        received = 0
        if not occluded and distance <= max_range_mm:
            received = event["source_level_milli"] * 1000 // (1000 + distance)
        observed.append({
            "event_id": event["event_id"],
            "source_tick": event["source_tick"],
            "age_ticks": state.tick - event["source_tick"],
            "distance_mm": distance,
            "received_level_milli": received,
            "occluded": occluded,
            "audible": received > 0 and received >= threshold_milli,
        })
    observed.sort(key=lambda item: (item["distance_mm"], item["event_id"]))
    result = {
        "schema": OBSERVATION_SCHEMA,
        "tick": state.tick,
        "room_revision": state.revision,
        "listener_position_mm": [lx, ly],
        "events": observed,
        "source_classification": "PUBLIC_SYNTHETIC_geometric_fixture_not_audio_or_acoustics",
    }
    wire = json.dumps(result, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    result["observation_sha256"] = sha256(wire).hexdigest()
    return result

__all__ = ["AcousticError", "EVENT_SCHEMA", "OBSERVATION_SCHEMA", "sense_sound_events"]
