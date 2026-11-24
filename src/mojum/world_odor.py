"""Bounded, deterministic synthetic odor-field observations for the point-room fixture.

This is an opt-in geometric fixture, not chemistry, CFD, a physical sensor, or
an authoritative behavior input. All coordinates are integer millimetres.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .world_room import Room, RoomState, _revision, _segment_hits_rect, _valid

SCHEMA = "mojum.world.odor-field/1"
PROFILE = "PUBLIC_SYNTHETIC"
_SCALE = 1_000_000_000
_MAX_SOURCES = 512
_MAX_POINTS = 4096
_MAX_ID = 96
_MAX_CONCENTRATION = 1_000_000_000
_MAX_COORD_MM = 1_000_000_000
_MAX_FALLOFF_MM = 1_000_000_000
_MAX_AGE_TICKS = 1_000_000


@dataclass(frozen=True)
class OdorSource:
    source_id: str
    x_mm: int
    y_mm: int
    concentration: int
    created_tick: int = 0
    decay_numerator: int = 1
    decay_denominator: int = 1


@dataclass(frozen=True)
class SamplePoint:
    point_id: str
    x_mm: int
    y_mm: int


@dataclass(frozen=True)
class OdorReading:
    point_id: str
    concentration: int


@dataclass(frozen=True)
class OdorObservation:
    schema: str
    profile: str
    tick: int
    room_revision: str
    readings: tuple[OdorReading, ...]
    digest: str


def _native_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _identifier(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= _MAX_ID
            and value.isascii() and value.isprintable()
            and value.strip() == value)


def _inside(room: Room, x_mm: int, y_mm: int) -> bool:
    return _valid(room, x_mm / 1000, y_mm / 1000)


def _decay_q(numerator: int, denominator: int, age: int) -> int:
    """Return floor((numerator/denominator)**age * SCALE), bounded and deterministic."""
    if age == 0 or numerator == denominator:
        return _SCALE
    if numerator == 0:
        return 0
    base = numerator * _SCALE // denominator
    result = _SCALE
    exponent = age
    while exponent:
        if exponent & 1:
            result = result * base // _SCALE
        exponent >>= 1
        if exponent:
            base = base * base // _SCALE
    return result


def _canonical_digest(tick: int, revision: str,
                      readings: tuple[OdorReading, ...]) -> str:
    payload = {
        "schema": SCHEMA,
        "profile": PROFILE,
        "tick": tick,
        "room_revision": revision,
        "readings": [[item.point_id, item.concentration] for item in readings],
    }
    wire = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")
    return hashlib.sha256(wire).hexdigest()


def sample_odor_field(
    room: Room,
    state: RoomState,
    sources: tuple[OdorSource, ...] | list[OdorSource],
    points: tuple[SamplePoint, ...] | list[SamplePoint],
    *,
    consent: bool,
    falloff_mm: int = 1000,
    occlusion: bool = True,
) -> OdorObservation:
    """Sample a deterministic integer odor field at explicit points.

    Contributions use inverse-square-like attenuation with a nonzero softening
    radius: floor(C * falloff² / (falloff² + distance²)). Logical-tick decay
    uses a fixed-point integer exponentiation. Overlapping sources are summed
    and saturated at MAX_CONCENTRATION.
    """
    if consent is not True or type(consent) is not bool:
        raise ValueError("explicit PUBLIC_SYNTHETIC consent is required")
    if type(room) is not Room or type(state) is not RoomState:
        raise ValueError("expected canonical Room and RoomState")
    if (not _native_int(state.tick, 0, _MAX_AGE_TICKS)
            or type(state.revision) is not str
            or state.revision != _revision(room, state.tick, state.x, state.y)
            or not _valid(room, state.x, state.y)):
        raise ValueError("stale or invalid RoomState")
    if type(falloff_mm) is not int or not 1 <= falloff_mm <= _MAX_FALLOFF_MM:
        raise ValueError("invalid falloff_mm")
    if type(occlusion) is not bool:
        raise ValueError("occlusion must be a native bool")
    if type(sources) not in (tuple, list) or not 1 <= len(sources) <= _MAX_SOURCES:
        raise ValueError("invalid source collection")
    if type(points) not in (tuple, list) or not 1 <= len(points) <= _MAX_POINTS:
        raise ValueError("invalid point collection")
    if len(sources) * len(points) > 100_000:
        raise ValueError("sampling work budget exceeded")

    source_ids: set[str] = set()
    normalized_sources: list[OdorSource] = []
    for source in sources:
        if type(source) is not OdorSource:
            raise ValueError("invalid source type")
        if (not _identifier(source.source_id) or source.source_id in source_ids
                or not _native_int(source.x_mm, 0, _MAX_COORD_MM)
                or not _native_int(source.y_mm, 0, _MAX_COORD_MM)
                or not _native_int(source.concentration, 0, _MAX_CONCENTRATION)
                or not _native_int(source.created_tick, 0, state.tick)
                or not _native_int(source.decay_numerator, 0, 1_000_000)
                or not _native_int(source.decay_denominator, 1, 1_000_000)
                or source.decay_numerator > source.decay_denominator
                or not _inside(room, source.x_mm, source.y_mm)):
            raise ValueError("invalid odor source")
        age = state.tick - source.created_tick
        if age > _MAX_AGE_TICKS:
            raise ValueError("source age exceeds bound")
        source_ids.add(source.source_id)
        normalized_sources.append(source)

    point_ids: set[str] = set()
    normalized_points: list[SamplePoint] = []
    for point in points:
        if type(point) is not SamplePoint:
            raise ValueError("invalid sample point type")
        if (not _identifier(point.point_id) or point.point_id in point_ids
                or not _native_int(point.x_mm, 0, _MAX_COORD_MM)
                or not _native_int(point.y_mm, 0, _MAX_COORD_MM)
                or not _inside(room, point.x_mm, point.y_mm)):
            raise ValueError("invalid sample point")
        point_ids.add(point.point_id)
        normalized_points.append(point)

    source_cache = []
    for source in sorted(normalized_sources, key=lambda item: item.source_id):
        age = state.tick - source.created_tick
        decay = _decay_q(source.decay_numerator, source.decay_denominator, age)
        source_cache.append((source, decay))

    readings = []
    radius_sq = falloff_mm * falloff_mm
    for point in sorted(normalized_points, key=lambda item: item.point_id):
        total = 0
        for source, decay in source_cache:
            if source.concentration == 0 or decay == 0:
                continue
            dx = point.x_mm - source.x_mm
            dy = point.y_mm - source.y_mm
            distance_sq = dx * dx + dy * dy
            if occlusion and any(
                _segment_hits_rect(source.x_mm / 1000, source.y_mm / 1000,
                                   point.x_mm / 1000, point.y_mm / 1000, rect)
                for rect in room.obstacles
            ):
                continue
            attenuated = source.concentration * radius_sq // (radius_sq + distance_sq)
            total += attenuated * decay // _SCALE
            if total >= _MAX_CONCENTRATION:
                total = _MAX_CONCENTRATION
                break
        readings.append(OdorReading(point.point_id, total))

    frozen_readings = tuple(readings)
    digest = _canonical_digest(state.tick, state.revision, frozen_readings)
    return OdorObservation(SCHEMA, PROFILE, state.tick, state.revision,
                           frozen_readings, digest)
