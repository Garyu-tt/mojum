"""Offline, non-authoritative join witness for three PUBLIC_SYNTHETIC sensors.

The witness audits a caller-provided window; it never reads a journal, observes
hardware, supplies an input to a neural model or modifies world/brain state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

SCHEMA = "mojum.neural.synthetic-sensor-time-join/1"
PROPRIO_SCHEMA = "mojum.developmental.proprioception/1"
PROPRIO_SOURCE = "synthetic_proprioception_not_geometry"
MAX_FRAMES = 4096
MAX_TICK = 9998  # canonical v1 Proprioception.tick is < 9999
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class SensorJoinError(ValueError):
    """Untrusted, stale, ill-typed or misaligned synthetic sensor window."""


@dataclass(frozen=True)
class SensorJoinWitness:
    schema: str
    start_tick: int
    frame_count: int
    dt_ns: int
    first_observed_at_ns: int
    last_observed_at_ns: int
    sha256: str


def _shape(value: object, keys: frozenset[str]) -> bool:
    # Never evaluate equality/hash on user-supplied mapping keys or subclasses.
    return (type(value) is dict and len(value) == len(keys)
            and all(type(k) is str for k in value)
            and set(value) == keys)


def _number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1000
    except (OverflowError, TypeError, ValueError):
        return False


def _integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _literal(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _canonical(value: object) -> bytes:
    # Inputs have been normalized into fixed-size, built-in primitive values.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def audit_sensor_time_frames(
    frames: list[dict] | tuple[dict, ...], *, dt_ns: int,
    start_tick: int = 0,
) -> SensorJoinWitness:
    """Validate a contiguous v1 interoception/world/proprioception window.

    frames is an ordered nonempty list/tuple. Each frame must have exactly:
      tick, interoception, world, proprioception.

    Only strict built-in, whitelisted PUBLIC_SYNTHETIC shapes are accepted.
    The digest covers validated metadata, NOT authenticated history; no payloads
    are retained. Every assertion is made before returning a witness.
    """
    if (type(frames) not in (tuple, list) or not 1 <= len(frames) <= MAX_FRAMES
            or not _integer(dt_ns, 1000, 1_000_000_000)
            or not _integer(start_tick, 0, MAX_TICK)
            or len(frames) > MAX_TICK - start_tick + 1):
        raise SensorJoinError("invalid sensor window budget")

    digest = hashlib.sha256()
    digest.update(_canonical([SCHEMA, start_tick, dt_ns, len(frames)]) + b"\n")
    last_revision = None
    fixed_light = None
    previous_beat_index = None
    for offset, frame in enumerate(frames):
        tick = start_tick + offset
        observed_at_ns = tick * dt_ns
        if not _shape(frame, frozenset(("tick", "interoception", "world", "proprioception"))):
            raise SensorJoinError("invalid joined frame shape")
        if not _integer(frame["tick"], tick, tick):
            raise SensorJoinError("noncontiguous sensor tick")
        intero = frame["interoception"]
        world = frame["world"]
        proprio = frame["proprioception"]
        if (not _shape(intero, frozenset(("kind", "observed_at_ns", "heartbeat")))
                or not _literal(intero["kind"], "interoception")
                or not _integer(intero["observed_at_ns"], observed_at_ns, observed_at_ns)):
            raise SensorJoinError("invalid interoception clock or shape")
        hb = intero["heartbeat"]
        if (not _shape(hb, frozenset(("beat_index", "phase_milli", "pulse", "synthetic")))
                or not _integer(hb["phase_milli"], 0, 999)
                or type(hb["pulse"]) is not bool
                or hb["synthetic"] is not True
                # In the supported virtual heartbeat, at most one beat can
                # occur per nanosecond. This checks pathological counters only;
                # it does NOT infer or certify a heart rate.
                or not _integer(hb["beat_index"], 0, observed_at_ns)):
            raise SensorJoinError("invalid synthetic heartbeat")
        if (previous_beat_index is not None
                and hb["beat_index"] < previous_beat_index):
            raise SensorJoinError("regressing heartbeat beat index")
        if (not _shape(world, frozenset(("kind", "light", "observed_at_ns")))
                or not _literal(world["kind"], "static-room-fixture")
                or type(world["light"]) is not str
                or world["light"] not in ("dark", "dim", "bright")
                or not _integer(world["observed_at_ns"], observed_at_ns, observed_at_ns)):
            raise SensorJoinError("invalid world clock or shape")
        if fixed_light is not None and world["light"] != fixed_light:
            raise SensorJoinError("static-room light changed mid-window")
        if (not _shape(proprio, frozenset(("schema", "tick", "world_revision",
                                          "position_xy", "heartbeat_pulse", "source")))
                or not _literal(proprio["schema"], PROPRIO_SCHEMA)
                or not _literal(proprio["source"], PROPRIO_SOURCE)
                or not _integer(proprio["tick"], tick, tick)
                or type(proprio["heartbeat_pulse"]) is not bool
                or proprio["heartbeat_pulse"] != hb["pulse"]
                or type(proprio["world_revision"]) is not str
                or _REVISION.fullmatch(proprio["world_revision"]) is None
                or type(proprio["position_xy"]) is not list
                or len(proprio["position_xy"]) != 2
                or not all(_number(x) for x in proprio["position_xy"])):
            raise SensorJoinError("invalid proprioception or pulse alignment")
        if proprio["world_revision"] == last_revision:
            # Canonical room revision binds tick, including stationary ticks.
            raise SensorJoinError("reused point-room tick revision")
        previous_beat_index = hb["beat_index"]
        fixed_light = world["light"]
        last_revision = proprio["world_revision"]
        # Untrusted nested dicts are never JSON-serialized. This fixed row
        # avoids custom __repr__/__json__ hooks and disallows data exfiltration.
        row = [tick, observed_at_ns, hb["beat_index"], hb["phase_milli"],
               hb["pulse"], fixed_light, last_revision,
               float(proprio["position_xy"][0]), float(proprio["position_xy"][1])]
        digest.update(_canonical(row) + b"\n")

    return SensorJoinWitness(
        schema=SCHEMA,
        start_tick=start_tick,
        frame_count=len(frames),
        dt_ns=dt_ns,
        first_observed_at_ns=start_tick * dt_ns,
        last_observed_at_ns=(start_tick + len(frames) - 1) * dt_ns,
        sha256=digest.hexdigest(),
    )
