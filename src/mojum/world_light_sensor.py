"""Bounded synthetic point-room illumination sensor.

A deterministic 2D fixture diagnostic, not photometric or physical light transport.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from .world_room import Room, RoomState, _revision

SCHEMA = "mojum.world.illumination/1"
MAX_SOURCES = 16
MAX_ROOM_METRES = 1_000_000.0
MAX_TICK = (1 << 63) - 1


class LightSensorError(ValueError):
    """Invalid, stale, or over-budget illumination input."""


@dataclass(frozen=True)
class IlluminationFrame:
    schema: str
    tick: int
    room_revision: str
    ambient: float
    direct: float
    total: float
    visible_sources: tuple[str, ...]
    occluded_sources: tuple[str, ...]
    contributions: tuple[tuple[str, float], ...]
    provenance: str
    physical_contact: bool
    digest: str


def _number(value: object, name: str, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise LightSensorError(f"{name} must be a native int or float")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise LightSensorError(f"{name} is outside the numeric range") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise LightSensorError(f"{name} is outside its finite range")
    return result


def _inside_closed_rect(x: float, y: float, rect: tuple) -> bool:
    left, bottom, width, height = rect
    return left <= x <= left + width and bottom <= y <= bottom + height


def _segment_hits_rect(x0: float, y0: float, x1: float, y1: float,
                       rect: tuple) -> bool:
    """Closed-AABB segment intersection; tangency counts as an opaque shadow."""
    left, bottom, width, height = rect
    dx, dy = x1 - x0, y1 - y0
    enter, leave = 0.0, 1.0
    for p, q in ((-dx, x0 - left),
                 (dx, left + width - x0),
                 (-dy, y0 - bottom),
                 (dy, bottom + height - y0)):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > leave:
                return False
            enter = max(enter, ratio)
        else:
            if ratio < enter:
                return False
            leave = min(leave, ratio)
    return enter <= leave


def _validate_room(room: object, state: object, tick: object,
                   expected_revision: object) -> tuple[Room, RoomState]:
    if type(room) is not Room or type(state) is not RoomState:
        raise LightSensorError("room and state must be canonical Room/RoomState instances")
    if (type(tick) is not int or not 0 <= tick <= MAX_TICK
            or type(state.tick) is not int or tick != state.tick):
        raise LightSensorError("tick must exactly match the room state")
    if (type(state.revision) is not str or len(state.revision) != 64
            or any(ch not in "0123456789abcdef" for ch in state.revision)):
        raise LightSensorError("invalid room revision")
    if type(expected_revision) is not str or expected_revision != state.revision:
        raise LightSensorError("stale room revision")
    width = _number(room.width, "room.width", 0.0, MAX_ROOM_METRES)
    height = _number(room.height, "room.height", 0.0, MAX_ROOM_METRES)
    if width <= 0.0 or height <= 0.0:
        raise LightSensorError("room dimensions must be positive")
    x = _number(state.x, "state.x", 0.0, width)
    y = _number(state.y, "state.y", 0.0, height)
    if type(room.obstacles) is not tuple or len(room.obstacles) > 4096:
        raise LightSensorError("invalid or over-budget obstacle collection")
    for rect in room.obstacles:
        if type(rect) is not tuple or len(rect) != 4:
            raise LightSensorError("invalid obstacle geometry")
        left = _number(rect[0], "obstacle.x", 0.0, width)
        bottom = _number(rect[1], "obstacle.y", 0.0, height)
        w = _number(rect[2], "obstacle.width", 0.0, width)
        h = _number(rect[3], "obstacle.height", 0.0, height)
        if w <= 0.0 or h <= 0.0 or left + w > width or bottom + h > height:
            raise LightSensorError("obstacle is outside room bounds")
        if _inside_closed_rect(x, y, rect):
            raise LightSensorError("sensor position intersects an obstacle")
    try:
        # Preserve original int/float JSON spelling when checking revision.
        actual_revision = _revision(room, tick, state.x, state.y)
    except Exception as exc:
        raise LightSensorError("room revision cannot be verified") from exc
    if actual_revision != state.revision:
        raise LightSensorError("room state revision does not match its contents")
    return room, state


def _parse_sources(sources: object, room: Room) -> tuple[tuple[str, float, float, float], ...]:
    if type(sources) is not tuple or len(sources) > MAX_SOURCES:
        raise LightSensorError("sources must be a tuple of at most 16 entries")
    parsed = []
    seen = set()
    for item in sources:
        if type(item) is not tuple or len(item) != 4:
            raise LightSensorError("each source must be (id, x, y, intensity)")
        source_id, raw_x, raw_y, raw_intensity = item
        if (type(source_id) is not str or not 1 <= len(source_id) <= 32
                or not source_id.isascii()
                or not all(ch.isalnum() or ch in "_-" for ch in source_id)):
            raise LightSensorError("source id must be 1..32 ASCII letters, digits, '_' or '-'")
        if source_id in seen:
            raise LightSensorError("duplicate source id")
        seen.add(source_id)
        x = _number(raw_x, "source.x", 0.0, min(room.width, MAX_ROOM_METRES))
        y = _number(raw_y, "source.y", 0.0, min(room.height, MAX_ROOM_METRES))
        intensity = _number(raw_intensity, "source.intensity", 0.0, 1.0)
        if any(_inside_closed_rect(x, y, rect) for rect in room.obstacles):
            raise LightSensorError("light source intersects an obstacle")
        parsed.append((source_id, x, y, intensity))
    return tuple(parsed)


def measure_illumination(room: Room, state: RoomState, *,
                         tick: int, expected_revision: str,
                         sources: tuple[tuple[str, float, float, float], ...],
                         ambient: float = 0.0) -> IlluminationFrame:
    """Measure normalized direct + ambient fixture illumination.

    A source contributes intensity / (1 + distance_squared) when its straight
    path is not intersected by a closed obstacle AABB. Visible source
    contributions and the final total are independently capped at 1.0.
    """
    room, state = _validate_room(room, state, tick, expected_revision)
    ambient_value = _number(ambient, "ambient", 0.0, 1.0)
    parsed = _parse_sources(sources, room)
    visible, occluded, contributions = [], [], []
    direct_sum = 0.0
    for source_id, sx, sy, intensity in parsed:
        shadowed = any(
            _segment_hits_rect(state.x, state.y, sx, sy, rect)
            for rect in room.obstacles
        )
        if shadowed:
            occluded.append(source_id)
            continue
        distance_sq = (state.x - sx) ** 2 + (state.y - sy) ** 2
        contribution = intensity / (1.0 + distance_sq)
        visible.append(source_id)
        contributions.append((source_id, contribution))
        direct_sum += contribution
    direct = min(1.0, direct_sum)
    total = min(1.0, ambient_value + direct)
    payload = {
        "schema": SCHEMA, "tick": tick, "room_revision": state.revision,
        "ambient": ambient_value, "direct": direct, "total": total,
        "visible_sources": visible, "occluded_sources": occluded,
        "contributions": contributions,
        "provenance": "public-synthetic-point-room-illumination/v1",
        "physical_contact": False,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode("ascii")
    return IlluminationFrame(
        schema=SCHEMA, tick=tick, room_revision=state.revision,
        ambient=ambient_value, direct=direct, total=total,
        visible_sources=tuple(visible), occluded_sources=tuple(occluded),
        contributions=tuple(contributions),
        provenance=payload["provenance"], physical_contact=False,
        digest=hashlib.sha256(canonical).hexdigest(),
    )
