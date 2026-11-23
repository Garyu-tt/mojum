"""Deterministic analytic airflow cues for the synthetic point-room fixture.

This is a bounded environmental field sampler, not computational fluid dynamics.
It has no I/O, network access, hidden clock, or actuation authority.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re

from .world_room import Room, RoomState, _revision, _valid

SCHEMA = "mojum.world.airflow/1"
PROVENANCE = "synthetic-point-room-airflow/v1"
MAX_SOURCES = 32
MAX_SOURCE_SPEED_MM_S = 20_000
MAX_RESULT_SPEED_MM_S = 100_000
MAX_ROOM_EDGE_MM = 1_000_000
MAX_TICK = (1 << 63) - 1
ATTENUATION_RANGE_MM = 10_000
WEIGHT_SCALE = 1_000_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}\Z", re.ASCII)


class AirflowError(ValueError):
    """Invalid or out-of-policy synthetic airflow request."""


@dataclass(frozen=True)
class AirflowSample:
    schema: str
    tick: int
    revision: str
    x_mm: int
    y_mm: int
    vx_mm_s: int
    vy_mm_s: int
    magnitude_mm_s: int
    contributing_source_ids: tuple[str, ...]
    occluded_source_ids: tuple[str, ...]
    digest: str
    provenance: str = PROVENANCE


def _mm(value, name):
    if type(value) not in (int, float):
        raise AirflowError(f"{name} must be a finite native number")
    try:
        if not math.isfinite(value):
            raise AirflowError(f"{name} must be a finite native number")
        scaled = value * 1000
        if not math.isfinite(scaled) or scaled != round(scaled):
            raise AirflowError(f"{name} must align to an integer millimetre")
        return int(round(scaled))
    except (OverflowError, TypeError, ValueError) as exc:
        if isinstance(exc, AirflowError):
            raise
        raise AirflowError(f"{name} must be a bounded millimetre value") from exc


def _orientation(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_segment(ax, ay, bx, by, px, py):
    return (min(ax, bx) <= px <= max(ax, bx)
            and min(ay, by) <= py <= max(ay, by))


def _segments_intersect(a, b, c, d):
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    o1 = _orientation(ax, ay, bx, by, cx, cy)
    o2 = _orientation(ax, ay, bx, by, dx, dy)
    o3 = _orientation(cx, cy, dx, dy, ax, ay)
    o4 = _orientation(cx, cy, dx, dy, bx, by)
    if o1 == 0 and _on_segment(ax, ay, bx, by, cx, cy):
        return True
    if o2 == 0 and _on_segment(ax, ay, bx, by, dx, dy):
        return True
    if o3 == 0 and _on_segment(cx, cy, dx, dy, ax, ay):
        return True
    if o4 == 0 and _on_segment(cx, cy, dx, dy, bx, by):
        return True
    return ((o1 < 0 < o2 or o2 < 0 < o1)
            and (o3 < 0 < o4 or o4 < 0 < o3))


def _inside_rect(px, py, rect):
    x, y, width, height = rect
    return x <= px <= x + width and y <= py <= y + height


def _blocked(room, source, sample):
    sx, sy = source
    px, py = sample
    for obstacle in room.obstacles:
        x, y, width, height = obstacle
        rect = (_mm(x, "obstacle.x"), _mm(y, "obstacle.y"),
                _mm(width, "obstacle.width"), _mm(height, "obstacle.height"))
        if _inside_rect(sx, sy, rect) or _inside_rect(px, py, rect):
            return True
        left, bottom, w, h = rect
        edges = (
            ((left, bottom), (left + w, bottom)),
            ((left + w, bottom), (left + w, bottom + h)),
            ((left + w, bottom + h), (left, bottom + h)),
            ((left, bottom + h), (left, bottom)),
        )
        if any(_segments_intersect((sx, sy), (px, py), *edge) for edge in edges):
            return True
    return False


def _round_div(numerator, denominator):
    """Integer division rounded to nearest, with halves away from zero."""
    sign = -1 if numerator < 0 else 1
    return sign * ((abs(numerator) + denominator // 2) // denominator)


def _admit_sources(sources, room_bounds):
    if type(sources) not in (tuple, list) or len(sources) > MAX_SOURCES:
        raise AirflowError("sources must be a bounded native list or tuple")
    admitted = []
    seen = set()
    width_mm, height_mm = room_bounds
    for index, item in enumerate(sources):
        if type(item) is not dict:
            raise AirflowError(f"source {index} must be a native dict")
        expected_keys = {"id", "x_mm", "y_mm", "vx_mm_s", "vy_mm_s"}
        # Reject oversized exact dicts before allocating/iterating a key snapshot.
        if len(item) != len(expected_keys):
            raise AirflowError(f"source {index} has an unsupported field set")
        keys = tuple(dict.keys(item))
        if (any(type(key) is not str for key in keys)
                or set(keys) != expected_keys):
            raise AirflowError(f"source {index} has an unsupported field set")
        source_id = item["id"]
        if type(source_id) is not str or not _ID.fullmatch(source_id):
            raise AirflowError(f"source {index} has an invalid id")
        if source_id in seen:
            raise AirflowError("duplicate source id")
        seen.add(source_id)
        x, y, vx, vy = (item["x_mm"], item["y_mm"],
                         item["vx_mm_s"], item["vy_mm_s"])
        if any(type(v) is not int for v in (x, y, vx, vy)):
            raise AirflowError(f"source {source_id} coordinates and velocity must be native integers")
        if not (0 <= x <= width_mm and 0 <= y <= height_mm):
            raise AirflowError(f"source {source_id} is outside the room")
        if max(abs(vx), abs(vy)) > MAX_SOURCE_SPEED_MM_S:
            raise AirflowError(f"source {source_id} exceeds the speed limit")
        admitted.append((source_id, x, y, vx, vy))
    return tuple(sorted(admitted, key=lambda row: row[0]))


def sample_room_airflow(room, state, *, tick, expected_revision,
                        x_mm, y_mm, sources, public_synthetic):
    """Sample a deterministic attenuated vector field at one room coordinate.

    The sources collection is a bounded native list/tuple of mappings with
    exactly id, x_mm, y_mm, vx_mm_s and vy_mm_s. Distances and output are
    integer millimetres and millimetres-per-second. A source is fully
    occluded when a closed obstacle intersects the source-to-sample segment.
    """
    if public_synthetic is not True:
        raise AirflowError("explicit PUBLIC_SYNTHETIC assertion is required")
    if type(room) is not Room or type(state) is not RoomState:
        raise AirflowError("canonical Room and RoomState instances are required")
    if (type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK
            or type(tick) is not int or not 0 <= tick <= MAX_TICK
            or tick != state.tick):
        raise AirflowError("sample tick must equal the bounded current room-state tick")
    if (type(expected_revision) is not str or len(expected_revision) != 64
            or type(state.revision) is not str or len(state.revision) != 64):
        raise AirflowError("stale or corrupted room revision")

    # Validate caller-constructible RoomState values before hashing them.
    # Otherwise json.dumps in _revision may process an enormous integer first.
    if not _valid(room, state.x, state.y):
        raise AirflowError("invalid room state")
    _mm(state.x, "state.x")
    _mm(state.y, "state.y")
    if (state.revision != expected_revision
            or state.revision != _revision(room, state.tick, state.x, state.y)):
        raise AirflowError("stale or corrupted room revision")

    width_mm, height_mm = _mm(room.width, "room.width"), _mm(room.height, "room.height")
    _mm(state.x, "state.x")
    _mm(state.y, "state.y")
    if not (0 < width_mm <= MAX_ROOM_EDGE_MM and 0 < height_mm <= MAX_ROOM_EDGE_MM):
        raise AirflowError("room dimensions exceed the bounded millimetre profile")
    for obstacle in room.obstacles:
        converted = tuple(_mm(value, f"obstacle.{label}")
                          for label, value in zip(("x", "y", "width", "height"), obstacle))
        if converted[2] <= 0 or converted[3] <= 0:
            raise AirflowError("obstacles must have positive millimetre dimensions")
    px, py = x_mm, y_mm
    if type(px) is not int or type(py) is not int:
        raise AirflowError("sample coordinates must be native integer millimetres")
    if not (0 <= px <= width_mm and 0 <= py <= height_mm):
        raise AirflowError("sample point is outside the room")
    if any(_inside_rect(px, py, (_mm(x, "obstacle.x"), _mm(y, "obstacle.y"),
                                _mm(w, "obstacle.width"), _mm(h, "obstacle.height")))
           for x, y, w, h in room.obstacles):
        raise AirflowError("sample point is inside a solid obstacle")

    rows = _admit_sources(sources, (width_mm, height_mm))
    vx_total = vy_total = 0
    contributing = []
    occluded = []
    range_sq = ATTENUATION_RANGE_MM * ATTENUATION_RANGE_MM
    for source_id, sx, sy, vx, vy in rows:
        if any(_inside_rect(sx, sy, (_mm(x, "obstacle.x"), _mm(y, "obstacle.y"),
                                    _mm(w, "obstacle.width"), _mm(h, "obstacle.height")))
               for x, y, w, h in room.obstacles):
            raise AirflowError(f"source {source_id} is inside a solid obstacle")
        if max(abs(vx), abs(vy)) == 0:
            continue
        if _blocked(room, (sx, sy), (px, py)):
            occluded.append(source_id)
            continue
        dx, dy = sx - px, sy - py
        distance_sq = dx * dx + dy * dy
        weight = (range_sq * WEIGHT_SCALE) // (range_sq + distance_sq)
        if weight == 0:
            continue
        vx_total += _round_div(vx * weight, WEIGHT_SCALE)
        vy_total += _round_div(vy * weight, WEIGHT_SCALE)
        contributing.append(source_id)

    magnitude_squared = vx_total * vx_total + vy_total * vy_total
    if magnitude_squared > MAX_RESULT_SPEED_MM_S * MAX_RESULT_SPEED_MM_S:
        raise AirflowError("combined airflow exceeds the bounded output speed")
    magnitude = math.isqrt(magnitude_squared)
    digest_payload = {
        "schema": SCHEMA,
        "tick": tick,
        "revision": expected_revision,
        "sample_mm": [px, py],
        "sources": [list(row) for row in rows],
        "vx_mm_s": vx_total,
        "vy_mm_s": vy_total,
        "contributing": contributing,
        "occluded": occluded,
    }
    canonical = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(canonical).hexdigest()
    return AirflowSample(
        schema=SCHEMA, tick=tick, revision=expected_revision,
        x_mm=px, y_mm=py, vx_mm_s=vx_total, vy_mm_s=vy_total,
        magnitude_mm_s=magnitude,
        contributing_source_ids=tuple(contributing),
        occluded_source_ids=tuple(occluded), digest=digest,
    )
