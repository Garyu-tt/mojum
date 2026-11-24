"""Bounded synthetic odor-field observations for the deterministic point-room.

This is a geometric fixture, not CFD, chemistry, or a physical sensor.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.world.odor/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_SOURCES = 32
MAX_POINTS = 256
MAX_ID_LENGTH = 64
MAX_CONCENTRATION = 1_000_000
MAX_TICK_AGE = 1_000_000
MAX_COORD_MM = 10_000_000
MAX_DISTANCE_SQUARED = 10**16


@dataclass(frozen=True)
class OdorProfile:
    """Detached immutable source field, bound to one room geometry."""
    room_digest: str
    created_tick: int
    decay_per_tick: int
    sources: tuple[tuple[str, int, int, int], ...]


def _native_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _room_geometry(room) -> tuple[int, int, tuple[tuple[int, int, int, int], ...]]:
    # Import lazily so this optional sensor does not affect room startup.
    from .world_room import Room
    if type(room) is not Room:
        raise ValueError("room must be the canonical Room")
    values = (room.width, room.height)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError("invalid room dimensions")
    width_mm, height_mm = (round(v * 1000) for v in values)
    if not (1 <= width_mm <= MAX_COORD_MM and 1 <= height_mm <= MAX_COORD_MM):
        raise ValueError("room dimensions exceed odor fixture bounds")
    obstacles = []
    for rect in room.obstacles:
        if type(rect) is not tuple or len(rect) != 4:
            raise ValueError("invalid room obstacle")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in rect):
            raise ValueError("invalid room obstacle")
        x, y, w, h = (round(v * 1000) for v in rect)
        obstacles.append((x, y, w, h))
    return width_mm, height_mm, tuple(obstacles)


def _room_digest(room) -> str:
    width, height, obstacles = _room_geometry(room)
    return _digest([width, height, obstacles])


def _inside(point_x: int, point_y: int, width: int, height: int) -> bool:
    return 0 <= point_x <= width and 0 <= point_y <= height


def _segment_intersects_rect(x0: int, y0: int, x1: int, y1: int,
                             rect: tuple[int, int, int, int]) -> bool:
    """Closed integer AABB intersection; touching an obstacle blocks the path."""
    left, bottom, width, height = rect
    dx, dy = x1 - x0, y1 - y0
    # Liang-Barsky with exact rational comparisons, avoiding float edge drift.
    low_num, low_den = 0, 1
    high_num, high_den = 1, 1
    for p, q in ((-dx, x0-left), (dx, left+width-x0),
                 (-dy, y0-bottom), (dy, bottom+height-y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        num, den = q, p
        if den < 0:
            num, den = -num, -den
        # For p < 0, q/p is a lower bound; for p > 0 it is an upper bound.
        if p < 0:
            if num * low_den > low_num * den:
                low_num, low_den = num, den
        else:
            if num * high_den < high_num * den:
                high_num, high_den = num, den
        if low_num * high_den > high_num * low_den:
            return False
    return True


def create_profile(room, *, created_tick: int, decay_per_tick: int,
                   sources, provenance: str = PROVENANCE,
                   consent: bool = True) -> OdorProfile:
    """Validate and detach a bounded synthetic source field."""
    width, height, _ = _room_geometry(room)
    if provenance != PROVENANCE or consent is not True:
        raise ValueError("PUBLIC_SYNTHETIC provenance and explicit consent required")
    if not _native_int(created_tick, 0, MAX_TICK_AGE):
        raise ValueError("invalid created_tick")
    if not _native_int(decay_per_tick, 0, MAX_CONCENTRATION):
        raise ValueError("invalid decay_per_tick")
    if type(sources) not in (list, tuple) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("invalid source collection")
    normalized = []
    seen = set()
    for source in sources:
        if type(source) is not dict or set(source) != {"id", "x_mm", "y_mm", "concentration"}:
            raise ValueError("source must contain exactly id, x_mm, y_mm, concentration")
        sid = source["id"]
        if type(sid) is not str or not sid or len(sid) > MAX_ID_LENGTH or sid.strip() != sid:
            raise ValueError("invalid source id")
        if sid in seen:
            raise ValueError("duplicate source id")
        seen.add(sid)
        x, y, concentration = source["x_mm"], source["y_mm"], source["concentration"]
        if not _native_int(x, 0, min(width, MAX_COORD_MM)) or not _native_int(y, 0, min(height, MAX_COORD_MM)):
            raise ValueError("source outside room or invalid coordinate")
        if not _native_int(concentration, 0, MAX_CONCENTRATION):
            raise ValueError("invalid concentration")
        normalized.append((sid, x, y, concentration))
    normalized.sort(key=lambda row: row[0])
    return OdorProfile(_room_digest(room), created_tick, decay_per_tick, tuple(normalized))


def sample(profile: OdorProfile, room, state, *, tick: int, expected_revision: str,
           points, occlusion: bool = True) -> dict:
    """Sample caller-supplied points; output is a detached deterministic receipt."""
    from .world_room import RoomState, _revision
    if type(profile) is not OdorProfile or type(state) is not RoomState:
        raise ValueError("invalid profile or room state")
    width, height, obstacles = _room_geometry(room)
    if profile.room_digest != _room_digest(room):
        raise ValueError("profile belongs to different room geometry")
    if type(state.tick) is not int or state.tick < 0 or type(tick) is not int or tick != state.tick:
        raise ValueError("sample tick must match current room state")
    if type(expected_revision) is not str or state.revision != expected_revision:
        raise ValueError("stale room revision")
    if state.revision != _revision(room, state.tick, state.x, state.y):
        raise ValueError("corrupt room state revision")
    if type(occlusion) is not bool:
        raise ValueError("occlusion must be bool")
    age = tick - profile.created_tick
    if age < 0 or age > MAX_TICK_AGE:
        raise ValueError("sample tick outside profile lifetime")
    if type(points) not in (list, tuple) or not 1 <= len(points) <= MAX_POINTS:
        raise ValueError("invalid sample point collection")
    normalized_points = []
    seen = set()
    for point in points:
        if type(point) is not dict or set(point) != {"id", "x_mm", "y_mm"}:
            raise ValueError("point must contain exactly id, x_mm, y_mm")
        pid = point["id"]
        x, y = point["x_mm"], point["y_mm"]
        if type(pid) is not str or not pid or len(pid) > MAX_ID_LENGTH or pid.strip() != pid or pid in seen:
            raise ValueError("invalid or duplicate point id")
        if not _native_int(x, 0, min(width, MAX_COORD_MM)) or not _native_int(y, 0, min(height, MAX_COORD_MM)):
            raise ValueError("sample point outside room or invalid coordinate")
        seen.add(pid)
        normalized_points.append((pid, x, y))
    normalized_points.sort(key=lambda row: row[0])

    rows = []
    for pid, px, py in normalized_points:
        total = 0
        contributions = []
        for sid, sx, sy, initial in profile.sources:
            remaining = max(0, initial - profile.decay_per_tick * age)
            dx, dy = px - sx, py - sy
            distance_sq = dx * dx + dy * dy
            if distance_sq > MAX_DISTANCE_SQUARED:
                raise ValueError("distance exceeds arithmetic budget")
            blocked = bool(occlusion and any(
                _segment_intersects_rect(sx, sy, px, py, obstacle)
                for obstacle in obstacles
            ))
            # One attenuation band per metre squared, integer-only.
            value = 0 if blocked else remaining // (1 + distance_sq // 1_000_000)
            total += value
            contributions.append({"source_id": sid, "value": value, "occluded": blocked})
        rows.append({"point_id": pid, "concentration": total, "contributions": contributions})
    payload = {
        "schema": SCHEMA, "provenance": PROVENANCE, "consent": True,
        "room_digest": profile.room_digest, "tick": tick, "revision": state.revision,
        "decay_per_tick": profile.decay_per_tick, "samples": rows,
        "physical_sensor": False,
    }
    return {**payload, "sha256": _digest(payload)}


def verify_receipt(receipt: object) -> bool:
    """Verify receipt integrity without treating its digest as authentication."""
    if type(receipt) is not dict or "sha256" not in receipt:
        return False
    digest = receipt.get("sha256")
    if type(digest) is not str or len(digest) != 64:
        return False
    payload = {key: value for key, value in receipt.items() if key != "sha256"}
    try:
        return _digest(payload) == digest
    except (TypeError, ValueError, OverflowError):
        return False
