"""Bounded synthetic temperature observations for public point-room fixtures."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, re
from typing import Any

SCHEMA = "mojum.world.synthetic-temperature/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_SOURCES, MAX_POINTS, MAX_ID_LENGTH = 64, 256, 48
MAX_COORD_MM, MAX_TICK, MAX_ABS_MILLI_C = 100_000, 2**31 - 1, 100_000

@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    point_id: str
    temperature_milli_c: int

@dataclass(frozen=True, slots=True)
class TemperatureReport:
    schema: str
    classification: str
    tick: int
    observations: tuple[TemperatureObservation, ...]
    digest: str

def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value

def _identifier(value: Any, name: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_LENGTH:
        raise ValueError(f"{name} must be a bounded identifier")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is None:
        raise ValueError(f"{name} contains unsupported characters")
    return value

def _records(value: Any, name: str, maximum: int) -> list[dict[str, Any]]:
    if type(value) not in (list, tuple) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} must contain 1..{maximum} records")
    if any(type(item) is not dict for item in value):
        raise ValueError(f"{name} entries must be plain dictionaries")
    return list(value)

def _require_exact_fields(row: dict[str, Any], required: set[str], name: str) -> None:
    # Reject oversized mappings in O(1) before iterating caller-controlled keys.
    if len(row) != len(required):
        raise ValueError(f"{name} fields do not match schema")
    # Exact native types are checked before any set construction/hashing.
    if any(type(key) is not str for key in row):
        raise ValueError(f"{name} keys must be native strings")
    if set(row) != required:
        raise ValueError(f"{name} fields do not match schema")

def observe_temperature(*, sources: Any, points: Any, baseline_milli_c: int,
                         tick: int, consent: bool) -> TemperatureReport:
    """Sample caller-supplied points. This is not a physical temperature model.

    Source fields: id, x_mm, y_mm, delta_milli_c,
    decay_milli_c_per_tick, observed_tick. Point fields: id, x_mm, y_mm.
    Distance attenuation is integer-only: magnitude / (1 + whole metres).
    """
    if consent is not True:
        raise ValueError("explicit consent=True is required")
    baseline = _integer(baseline_milli_c, "baseline_milli_c", -MAX_ABS_MILLI_C, MAX_ABS_MILLI_C)
    now = _integer(tick, "tick", 0, MAX_TICK)
    source_rows, point_rows = _records(sources, "sources", MAX_SOURCES), _records(points, "points", MAX_POINTS)
    source_ids, normalized_sources = set(), []
    required = {"id", "x_mm", "y_mm", "delta_milli_c", "decay_milli_c_per_tick", "observed_tick"}
    for row in source_rows:
        _require_exact_fields(row, required, "source")
        sid = _identifier(row["id"], "source id")
        if sid in source_ids:
            raise ValueError("duplicate source id")
        source_ids.add(sid)
        normalized_sources.append((
            sid, _integer(row["x_mm"], "source x_mm", 0, MAX_COORD_MM),
            _integer(row["y_mm"], "source y_mm", 0, MAX_COORD_MM),
            _integer(row["delta_milli_c"], "delta_milli_c", -MAX_ABS_MILLI_C, MAX_ABS_MILLI_C),
            _integer(row["decay_milli_c_per_tick"], "decay_milli_c_per_tick", 0, MAX_ABS_MILLI_C),
            _integer(row["observed_tick"], "observed_tick", 0, now)))
    point_ids, normalized_points = set(), []
    for row in point_rows:
        _require_exact_fields(row, {"id", "x_mm", "y_mm"}, "point")
        pid = _identifier(row["id"], "point id")
        if pid in point_ids:
            raise ValueError("duplicate point id")
        point_ids.add(pid)
        normalized_points.append((pid, _integer(row["x_mm"], "point x_mm", 0, MAX_COORD_MM),
                                  _integer(row["y_mm"], "point y_mm", 0, MAX_COORD_MM)))
    observations = []
    for pid, px, py in sorted(normalized_points):
        temperature = baseline
        for _, sx, sy, delta, decay, born in sorted(normalized_sources):
            magnitude = max(0, abs(delta) - decay * (now - born))
            distance = math.isqrt((px - sx) ** 2 + (py - sy) ** 2)
            contribution = magnitude // (1 + distance // 1000)
            temperature += contribution if delta >= 0 else -contribution
        if not -MAX_ABS_MILLI_C <= temperature <= MAX_ABS_MILLI_C:
            raise ValueError("combined temperature exceeds supported range")
        observations.append(TemperatureObservation(pid, temperature))
    payload = {"schema": SCHEMA, "classification": CLASSIFICATION, "tick": now,
               "observations": [{"point_id": x.point_id, "temperature_milli_c": x.temperature_milli_c}
                                for x in observations]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return TemperatureReport(SCHEMA, CLASSIFICATION, now, tuple(observations), hashlib.sha256(encoded).hexdigest())
