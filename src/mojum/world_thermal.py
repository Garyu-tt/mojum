"""Deterministic ambient thermal observations for the synthetic point-room."""
from dataclasses import dataclass
import hashlib
import json

MAX_POINTS = 256
MAX_ROOM_MM = 1_000_000
MAX_ABS_TEMP_MILLIC = 200_000
MAX_GRADIENT_MILLIC_PER_M = 20_000
MAX_CYCLE_TICKS = 10_000_000


class ThermalFieldError(ValueError):
    """Invalid, stale, or over-budget thermal observation request."""


@dataclass(frozen=True)
class ThermalProfile:
    """Integer-only profile; gradients are milli-Celsius per metre."""
    base_millic: int = 20_000
    gradient_x_millic_per_m: int = 0
    gradient_y_millic_per_m: int = 0
    cycle_period_ticks: int = 0
    cycle_amplitude_millic: int = 0
    max_abs_temp_millic: int = 100_000


def _native_int(value, low, high):
    return type(value) is int and low <= value <= high


def _revision(room, state):
    payload = [room.width, room.height, room.obstacles, state.tick, state.x, state.y]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _triangle_offset(tick, period, amplitude):
    if period == 0 or amplitude == 0:
        return 0
    phase = tick % period
    half = period // 2
    if half == 0:
        return 0
    rising = phase if phase <= half else period - phase
    return (amplitude * rising) // half - amplitude // 2


def observe_thermal_field(room, state, *, tick, expected_revision, profile, points_mm):
    """Evaluate bounded integer-mm samples at the current logical room tick."""
    from .world_room import Room, RoomState

    if type(room) is not Room or type(state) is not RoomState:
        raise ThermalFieldError("canonical Room and RoomState required")
    if type(state.tick) is not int or state.tick < 0 or type(tick) is not int or tick != state.tick:
        raise ThermalFieldError("thermal tick must equal current room tick")
    if (type(expected_revision) is not str or type(state.revision) is not str
            or state.revision != expected_revision
            or state.revision != _revision(room, state)):
        raise ThermalFieldError("stale or corrupted room revision")
    if type(profile) is not ThermalProfile:
        raise ThermalFieldError("exact ThermalProfile required")
    if not (
        _native_int(profile.base_millic, -MAX_ABS_TEMP_MILLIC, MAX_ABS_TEMP_MILLIC)
        and _native_int(profile.gradient_x_millic_per_m, -MAX_GRADIENT_MILLIC_PER_M, MAX_GRADIENT_MILLIC_PER_M)
        and _native_int(profile.gradient_y_millic_per_m, -MAX_GRADIENT_MILLIC_PER_M, MAX_GRADIENT_MILLIC_PER_M)
        and _native_int(profile.cycle_period_ticks, 0, MAX_CYCLE_TICKS)
        and _native_int(profile.cycle_amplitude_millic, 0, MAX_ABS_TEMP_MILLIC)
        and _native_int(profile.max_abs_temp_millic, 1, MAX_ABS_TEMP_MILLIC)
    ):
        raise ThermalFieldError("invalid thermal profile bounds")
    if (profile.cycle_period_ticks == 0) != (profile.cycle_amplitude_millic == 0):
        raise ThermalFieldError("cycle period and amplitude must both be zero or nonzero")
    if type(points_mm) not in (list, tuple) or not 1 <= len(points_mm) <= MAX_POINTS:
        raise ThermalFieldError("point collection must contain 1..256 points")

    width_mm, height_mm = int(room.width * 1000), int(room.height * 1000)
    cycle = _triangle_offset(tick, profile.cycle_period_ticks, profile.cycle_amplitude_millic)
    samples = []
    for point in points_mm:
        if type(point) not in (list, tuple) or len(point) != 2:
            raise ThermalFieldError("each point must be an [x_mm, y_mm] pair")
        x_mm, y_mm = point
        if not (_native_int(x_mm, 0, min(width_mm, MAX_ROOM_MM))
                and _native_int(y_mm, 0, min(height_mm, MAX_ROOM_MM))):
            raise ThermalFieldError("point outside room or invalid integer coordinate")
        if any(a * 1000 <= x_mm <= (a + w) * 1000 and
               b * 1000 <= y_mm <= (b + h) * 1000 for a, b, w, h in room.obstacles):
            raise ThermalFieldError("point lies inside a closed obstacle")
        temperature = (profile.base_millic
                       + profile.gradient_x_millic_per_m * x_mm // 1000
                       + profile.gradient_y_millic_per_m * y_mm // 1000
                       + cycle)
        if abs(temperature) > profile.max_abs_temp_millic:
            raise ThermalFieldError("temperature exceeds configured absolute bound")
        samples.append({"x_mm": x_mm, "y_mm": y_mm, "temperature_millic": temperature})

    body = {
        "schema": "mojum.world-thermal/1", "tick": tick,
        "room_revision": state.revision,
        "profile": {
            "base_millic": profile.base_millic,
            "gradient_x_millic_per_m": profile.gradient_x_millic_per_m,
            "gradient_y_millic_per_m": profile.gradient_y_millic_per_m,
            "cycle_period_ticks": profile.cycle_period_ticks,
            "cycle_amplitude_millic": profile.cycle_amplitude_millic,
            "max_abs_temp_millic": profile.max_abs_temp_millic,
        },
        "samples": samples,
        "provenance": "PUBLIC_SYNTHETIC/deterministic-room-thermal/v1",
        "physical_sensor": False,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**body, "digest_sha256": hashlib.sha256(canonical).hexdigest()}
