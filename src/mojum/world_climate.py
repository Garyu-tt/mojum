"""Deterministic synthetic ambient-climate sensor for the point-room fixture.

This is an opt-in read-only field sampler, not a thermodynamics solver.
"""
from dataclasses import dataclass
from .world_room import Room, RoomState, _revision, _valid

PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
MAX_TICK = (1 << 63) - 1
MIN_TEMPERATURE_MILLIC = -100_000
MAX_TEMPERATURE_MILLIC = 100_000
MAX_GRADIENT_MILLIC_PER_M = 100_000


class ClimateError(ValueError):
    """Invalid synthetic climate input or stale room observation."""


@dataclass(frozen=True)
class ClimateFrame:
    """Detached climate observation at one room revision and logical tick."""

    tick: int
    x: int | float
    y: int | float
    temperature_millic: int
    humidity_permille: int
    room_revision: str
    provenance: str = PUBLIC_SYNTHETIC


def _rounded_product(value: int, coordinate: int | float) -> int:
    """Round value*coordinate to nearest integer; exact ties go away from zero.

    Using the coordinate's exact integer ratio avoids platform-dependent
    floating point intermediate rounding for the integer climate output.
    """
    numerator, denominator = coordinate.as_integer_ratio()
    product = value * numerator
    magnitude, remainder = divmod(abs(product), denominator)
    if remainder * 2 >= denominator:
        magnitude += 1
    return magnitude if product >= 0 else -magnitude


def sample_room_climate(
    room: Room,
    state: RoomState,
    *,
    base_temperature_millic: int,
    gradient_x_millic_per_m: int = 0,
    gradient_y_millic_per_m: int = 0,
    humidity_permille: int = 500,
    provenance: str,
) -> ClimateFrame:
    """Sample a bounded affine temperature field and uniform relative humidity.

    Temperature is stored in milli-degrees Celsius. Humidity is relative
    humidity in permille (0..1000). All parameters are caller supplied;
    there is no clock, randomness, I/O, hidden world state or mutation.
    """
    if type(provenance) is not str or provenance != PUBLIC_SYNTHETIC:
        raise ClimateError("explicit PUBLIC_SYNTHETIC provenance required")
    if type(room) is not Room or type(state) is not RoomState:
        raise ClimateError("canonical Room and RoomState instances required")
    if (type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK
            or type(state.revision) is not str
            or len(state.revision) != 64
            or any(c not in "0123456789abcdef" for c in state.revision)):
        raise ClimateError("invalid room state metadata")
    if any(type(v) is not int for v in (
        base_temperature_millic, gradient_x_millic_per_m,
        gradient_y_millic_per_m, humidity_permille
    )):
        raise ClimateError("climate parameters must be native integers")
    if not MIN_TEMPERATURE_MILLIC <= base_temperature_millic <= MAX_TEMPERATURE_MILLIC:
        raise ClimateError("base temperature outside supported range")
    if (abs(gradient_x_millic_per_m) > MAX_GRADIENT_MILLIC_PER_M
            or abs(gradient_y_millic_per_m) > MAX_GRADIENT_MILLIC_PER_M):
        raise ClimateError("temperature gradient outside supported range")
    if not 0 <= humidity_permille <= 1000:
        raise ClimateError("humidity must be in 0..1000 permille")

    try:
        if not _valid(room, state.x, state.y):
            raise ClimateError("room position is invalid or obstructed")
        canonical_revision = _revision(room, state.tick, state.x, state.y)
    except ClimateError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ClimateError("room state cannot be validated") from exc
    if state.revision != canonical_revision:
        raise ClimateError("stale or corrupted room revision")

    try:
        delta_x = _rounded_product(gradient_x_millic_per_m, state.x)
        delta_y = _rounded_product(gradient_y_millic_per_m, state.y)
    except (AttributeError, OverflowError, TypeError, ValueError) as exc:
        raise ClimateError("room coordinates are invalid") from exc

    temperature = base_temperature_millic + delta_x + delta_y
    if not MIN_TEMPERATURE_MILLIC <= temperature <= MAX_TEMPERATURE_MILLIC:
        raise ClimateError("sampled temperature outside supported range")
    return ClimateFrame(
        tick=state.tick,
        x=state.x,
        y=state.y,
        temperature_millic=temperature,
        humidity_permille=humidity_permille,
        room_revision=state.revision,
    )
