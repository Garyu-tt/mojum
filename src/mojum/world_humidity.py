"""Bounded synthetic relative-humidity observations for point-room fixtures."""
from dataclasses import dataclass
import hashlib
import json
import re

SCHEMA = "mojum.world.humidity-profile/1"
OBSERVATION_SCHEMA = "mojum.world.humidity-observation/1"
MAX_TICK = (1 << 53) - 1
MAX_DIMENSION_MM = 1_000_000_000
MAX_GRADIENT = 100_000
MAX_TICK_DRIFT = 1_000
_HEX = re.compile(r"[0-9a-f]{64}\\Z", re.ASCII)


@dataclass(frozen=True)
class HumidityProfile:
    """Integer-only synthetic field; humidity values use milli-percent."""
    room_revision: str
    width_mm: int
    height_mm: int
    base_milli_percent: int
    gradient_x_milli_percent_per_km: int = 0
    gradient_y_milli_percent_per_km: int = 0
    drift_milli_percent_per_tick: int = 0
    epoch_tick: int = 0


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _profile(profile):
    if type(profile) is not HumidityProfile:
        raise ValueError("invalid humidity profile type")
    if (type(profile.room_revision) is not str or _HEX.fullmatch(profile.room_revision) is None
            or not _integer(profile.width_mm, 1, MAX_DIMENSION_MM)
            or not _integer(profile.height_mm, 1, MAX_DIMENSION_MM)
            or not _integer(profile.base_milli_percent, 0, 100_000)
            or not _integer(profile.gradient_x_milli_percent_per_km, -MAX_GRADIENT, MAX_GRADIENT)
            or not _integer(profile.gradient_y_milli_percent_per_km, -MAX_GRADIENT, MAX_GRADIENT)
            or not _integer(profile.drift_milli_percent_per_tick, -MAX_TICK_DRIFT, MAX_TICK_DRIFT)
            or not _integer(profile.epoch_tick, 0, MAX_TICK)):
        raise ValueError("invalid humidity profile fields")
    return profile


def _digest(payload):
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def observe_humidity(profile: HumidityProfile, *, room_revision: str,
                     tick: int, x_mm: int, y_mm: int) -> dict:
    """Sample an affine synthetic field using only integer arithmetic.

    Spatial coordinates use the half-open room domain [0, width) x [0, height).
    """
    p = _profile(profile)
    if (type(room_revision) is not str or _HEX.fullmatch(room_revision) is None
            or room_revision != p.room_revision):
        raise ValueError("stale or mismatched room revision")
    if (not _integer(tick, p.epoch_tick, MAX_TICK)
            or not _integer(x_mm, 0, p.width_mm - 1)
            or not _integer(y_mm, 0, p.height_mm - 1)):
        raise ValueError("tick or sample position outside profile bounds")
    raw = (p.base_milli_percent
           + (p.gradient_x_milli_percent_per_km * x_mm) // 1_000_000
           + (p.gradient_y_milli_percent_per_km * y_mm) // 1_000_000
           + p.drift_milli_percent_per_tick * (tick - p.epoch_tick))
    if not 0 <= raw <= 100_000:
        raise ValueError("computed relative humidity outside 0..100 percent")
    body = {
        "schema": OBSERVATION_SCHEMA, "profile_schema": SCHEMA,
        "room_revision": p.room_revision, "tick": tick,
        "position_mm": [x_mm, y_mm],
        "relative_humidity_milli_percent": raw,
        "provenance": "public-synthetic-humidity-field/v1",
        "physical_sensor": False,
    }
    return {**body, "digest": _digest(body)}


def verify_observation(observation: dict) -> bool:
    """Verify exact wire shape and canonical digest; this is not authentication."""
    keys = {"schema", "profile_schema", "room_revision", "tick", "position_mm",
            "relative_humidity_milli_percent", "provenance", "physical_sensor", "digest"}
    if type(observation) is not dict or any(type(k) is not str for k in observation):
        raise ValueError("invalid observation object")
    if set(observation) != keys:
        raise ValueError("invalid observation fields")
    if (type(observation["schema"]) is not str or observation["schema"] != OBSERVATION_SCHEMA
            or type(observation["profile_schema"]) is not str or observation["profile_schema"] != SCHEMA
            or type(observation["room_revision"]) is not str or _HEX.fullmatch(observation["room_revision"]) is None
            or not _integer(observation["tick"], 0, MAX_TICK)
            or type(observation["position_mm"]) is not list or len(observation["position_mm"]) != 2
            or not all(_integer(v, 0, MAX_DIMENSION_MM - 1) for v in observation["position_mm"])
            or not _integer(observation["relative_humidity_milli_percent"], 0, 100_000)
            or type(observation["provenance"]) is not str or observation["provenance"] != "public-synthetic-humidity-field/v1"
            or type(observation["physical_sensor"]) is not bool or observation["physical_sensor"] is not False
            or type(observation["digest"]) is not str or _HEX.fullmatch(observation["digest"]) is None):
        raise ValueError("invalid observation values")
    body = {key: value for key, value in observation.items() if key != "digest"}
    return _digest(body) == observation["digest"]
