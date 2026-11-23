"""CPU-only, deterministic virtual daylight fixture for synthetic Mojum rooms.

This module does not depend on wall time, geography, neural rhythms or real light.
Snapshots are integrity checks for detached fixtures, not authentication.
"""

from dataclasses import dataclass
import hashlib
import json

SCHEMA = "mojum.world.daylight/1"
PHASES = ("dawn", "day", "dusk", "night")
MAX_CYCLE_TICKS = 1_000_000
MAX_TICK = 2**53 - 1  # Exact positive integer domain in ordinary JSON clients.
MAX_SNAPSHOT_BYTES = 4096


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class DaylightCycle:
    """Four strictly positive phase durations and a cycle-local starting offset."""

    dawn_ticks: int = 30
    day_ticks: int = 90
    dusk_ticks: int = 30
    night_ticks: int = 90
    offset_ticks: int = 0

    def __post_init__(self) -> None:
        durations = (self.dawn_ticks, self.day_ticks, self.dusk_ticks, self.night_ticks)
        if any(type(n) is not int or n < 1 or n > MAX_CYCLE_TICKS for n in durations):
            raise ValueError("phase durations must be bounded positive integers")
        total = sum(durations)
        if total > MAX_CYCLE_TICKS:
            raise ValueError("cycle exceeds tick budget")
        if type(self.offset_ticks) is not int or not 0 <= self.offset_ticks < total:
            raise ValueError("invalid cycle offset")

    @property
    def period(self) -> int:
        return self.dawn_ticks + self.day_ticks + self.dusk_ticks + self.night_ticks

    def as_dict(self) -> dict:
        _check_cycle(self)
        return {"dawn_ticks": self.dawn_ticks, "day_ticks": self.day_ticks,
                "dusk_ticks": self.dusk_ticks, "night_ticks": self.night_ticks,
                "offset_ticks": self.offset_ticks}

    @property
    def revision(self) -> str:
        return _sha([SCHEMA, self.as_dict()])


@dataclass(frozen=True)
class DaylightState:
    tick: int
    revision: str


def _check_cycle(cycle: DaylightCycle) -> None:
    if type(cycle) is not DaylightCycle:
        raise ValueError("expected a native daylight cycle")
    # Frozen instances are not protected against object.__setattr__ mutations.
    cycle.__post_init__()


def _check_tick(tick: int) -> None:
    if type(tick) is not int or not 0 <= tick <= MAX_TICK:
        raise ValueError("invalid logical tick")


def _state_revision(cycle: DaylightCycle, tick: int) -> str:
    return _sha([SCHEMA, cycle.revision, tick])


def _check_state(cycle: DaylightCycle, state: DaylightState) -> None:
    _check_cycle(cycle)
    if type(state) is not DaylightState:
        raise ValueError("expected native daylight state")
    # Native dataclass identity alone does not guarantee initialized fields:
    # object.__new__(DaylightState) bypasses __init__ entirely.
    try:
        tick, revision = state.tick, state.revision
    except AttributeError as exc:
        raise ValueError("malformed daylight state") from exc
    _check_tick(tick)
    if type(revision) is not str or revision != _state_revision(cycle, tick):
        raise ValueError("daylight revision does not match the cycle and tick")


def observation(cycle: DaylightCycle, *, tick: int) -> dict:
    """Pure projection of a logical tick; no wall clock or external observations."""
    _check_cycle(cycle)
    _check_tick(tick)
    offset = (tick + cycle.offset_ticks) % cycle.period
    durations = (cycle.dawn_ticks, cycle.day_ticks, cycle.dusk_ticks, cycle.night_ticks)
    phase_offset = offset
    for index, duration in enumerate(durations):
        if phase_offset < duration:
            phase = PHASES[index]
            break
        phase_offset -= duration
    else:  # Guard invariant if phase definitions change.
        raise ValueError("invalid cycle phase")
    if phase == "dawn":
        light = 1000 * (phase_offset + 1) // duration
    elif phase == "day":
        light = 1000
    elif phase == "dusk":
        light = 1000 * (duration - phase_offset - 1) // duration
    else:
        light = 0
    return {"schema": SCHEMA, "tick": tick, "cycle_revision": cycle.revision,
            "state_revision": _state_revision(cycle, tick),
            "cycle_index": (tick + cycle.offset_ticks) // cycle.period,
            "phase": phase, "phase_tick": phase_offset,
            "phase_duration_ticks": duration, "light_permille": light,
            "provenance": "synthetic-logical-clock-only"}


def initial_state(cycle: DaylightCycle) -> DaylightState:
    _check_cycle(cycle)
    return DaylightState(0, _state_revision(cycle, 0))


def advance(cycle: DaylightCycle, state: DaylightState, *, tick: int,
            expected_revision: str) -> tuple[DaylightState, dict]:
    """CAS-protected one-tick transition, compatible with world_room tick rules."""
    _check_state(cycle, state)
    _check_tick(tick)
    if tick != state.tick + 1:
        raise ValueError("non-consecutive tick")
    if type(expected_revision) is not str or expected_revision != state.revision:
        raise ValueError("stale daylight revision")
    next_state = DaylightState(tick, _state_revision(cycle, tick))
    result = observation(cycle, tick=tick)
    result["phase_changed"] = result["phase"] != observation(cycle, tick=state.tick)["phase"]
    return next_state, result


def snapshot(cycle: DaylightCycle, state: DaylightState) -> str:
    """Canonical, bounded, transportable snapshot; NOT a trusted root."""
    _check_state(cycle, state)
    return _canonical({"schema": SCHEMA, "cycle": cycle.as_dict(),
                       "cycle_revision": cycle.revision,
                       "state": {"tick": state.tick, "revision": state.revision}})


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def restore(raw: str) -> tuple[DaylightCycle, DaylightState]:
    """Strict offline fixture restore; rejects ambiguous or forged wire fields."""
    if type(raw) is not str:
        raise ValueError("snapshot must be text")
    try:
        wire = raw.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid UTF-8 snapshot") from exc
    if len(wire) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds byte budget")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _v: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid snapshot JSON") from exc
    if type(value) is not dict or set(value) != {"schema", "cycle", "cycle_revision", "state"}:
        raise ValueError("invalid snapshot fields")
    if type(value["schema"]) is not str or value["schema"] != SCHEMA:
        raise ValueError("unsupported daylight schema")
    c = value["cycle"]
    if type(c) is not dict or set(c) != {
        "dawn_ticks", "day_ticks", "dusk_ticks", "night_ticks", "offset_ticks"
    }:
        raise ValueError("invalid cycle fields")
    cycle = DaylightCycle(**c)
    s = value["state"]
    if type(s) is not dict or set(s) != {"tick", "revision"}:
        raise ValueError("invalid daylight state fields")
    state = DaylightState(s["tick"], s["revision"])
    _check_state(cycle, state)
    if (type(value["cycle_revision"]) is not str
            or value["cycle_revision"] != cycle.revision):
        raise ValueError("invalid cycle revision")
    if raw != snapshot(cycle, state):
        raise ValueError("noncanonical daylight snapshot")
    return cycle, state
