"""Deterministic synthetic autonomic/homeostasis advisory contract.

This module regulates caller-supplied PUBLIC_SYNTHETIC signals.  Its fixed-
point outputs are engineering fixtures, not diagnoses, biological vital signs,
medical guidance, a consciousness model, or hardware control authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Final


PROFILE_SCHEMA: Final = "mojum.mikoshi.autonomic-profile/1"
OBSERVATION_SCHEMA: Final = "mojum.mikoshi.autonomic-observation/1"
STATE_SCHEMA: Final = "mojum.mikoshi.autonomic-state/1"
RESULT_SCHEMA: Final = "mojum.mikoshi.autonomic-result/1"
SNAPSHOT_SCHEMA: Final = "mojum.mikoshi.autonomic-snapshot/1"
CLASSIFICATION: Final = "PUBLIC_SYNTHETIC"

MAX_TICK: Final = 2**63 - 1
MAX_COUNTER: Final = 2**63 - 1
MAX_CADENCE_TICKS: Final = 1_000_000
MAX_SIGNAL_MILLI: Final = 1_000
MAX_THERMAL_DEVIATION_MILLI: Final = 10_000
MAX_GAIN_MILLI: Final = 10_000


class HomeostasisError(ValueError):
    """Invalid profile, observation, state, or transition."""


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise HomeostasisError(
            f"{name} must be a native integer in [{minimum}, {maximum}]"
        )
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise HomeostasisError(f"{name} must be a native bool")
    return value


def _increment(value: int, name: str) -> int:
    if value == MAX_COUNTER:
        raise HomeostasisError(f"{name} counter overflow")
    return value + 1


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value))


def _divide_toward_zero(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def _move_toward(current: int, target: int, maximum_delta: int) -> int:
    return _clamp(target, current - maximum_delta, current + maximum_delta)


def _exact_dict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if type(value) is not dict or len(value) != len(fields):
        raise HomeostasisError(f"invalid {name}")
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > 32 for key in keys):
        raise HomeostasisError(f"invalid {name} keys")
    if set(keys) != fields:
        raise HomeostasisError(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True)
class AutonomicProfile:
    schema: str
    enabled: bool
    cadence_ticks: int
    stale_after_ticks: int
    rhythm_period_ticks: int
    rhythm_amplitude_milli: int
    base_arousal_milli: int
    energy_target_milli: int
    load_target_milli: int
    energy_gain_milli: int
    load_gain_milli: int
    thermal_gain_milli: int
    max_delta_milli: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != PROFILE_SCHEMA:
            raise HomeostasisError("unsupported autonomic profile schema")
        _boolean(self.enabled, "enabled")
        _integer(self.cadence_ticks, "cadence_ticks", 1, MAX_CADENCE_TICKS)
        _integer(
            self.stale_after_ticks,
            "stale_after_ticks",
            0,
            MAX_CADENCE_TICKS,
        )
        _integer(
            self.rhythm_period_ticks,
            "rhythm_period_ticks",
            2,
            MAX_CADENCE_TICKS,
        )
        _integer(
            self.rhythm_amplitude_milli,
            "rhythm_amplitude_milli",
            0,
            MAX_SIGNAL_MILLI,
        )
        _integer(
            self.base_arousal_milli,
            "base_arousal_milli",
            0,
            MAX_SIGNAL_MILLI,
        )
        _integer(
            self.energy_target_milli,
            "energy_target_milli",
            0,
            MAX_SIGNAL_MILLI,
        )
        _integer(
            self.load_target_milli,
            "load_target_milli",
            0,
            MAX_SIGNAL_MILLI,
        )
        gains = (
            _integer(
                self.energy_gain_milli,
                "energy_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
            _integer(
                self.load_gain_milli,
                "load_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
            _integer(
                self.thermal_gain_milli,
                "thermal_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
        )
        if not any(gains) and self.rhythm_amplitude_milli == 0:
            raise HomeostasisError("autonomic profile has no active feedback signal")
        _integer(
            self.max_delta_milli,
            "max_delta_milli",
            1,
            MAX_SIGNAL_MILLI,
        )


@dataclass(frozen=True, slots=True)
class AutonomicObservation:
    schema: str
    classification: str
    tick: int
    revision: int
    energy_milli: int
    load_milli: int
    thermal_deviation_milli: int
    fault: bool = False

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != OBSERVATION_SCHEMA:
            raise HomeostasisError("unsupported autonomic observation schema")
        if type(self.classification) is not str or self.classification != CLASSIFICATION:
            raise HomeostasisError("autonomic input must be PUBLIC_SYNTHETIC")
        _integer(self.tick, "observation tick", 0, MAX_TICK)
        _integer(self.revision, "observation revision", 0, MAX_COUNTER)
        _integer(self.energy_milli, "energy_milli", 0, MAX_SIGNAL_MILLI)
        _integer(self.load_milli, "load_milli", 0, MAX_SIGNAL_MILLI)
        _integer(
            self.thermal_deviation_milli,
            "thermal_deviation_milli",
            -MAX_THERMAL_DEVIATION_MILLI,
            MAX_THERMAL_DEVIATION_MILLI,
        )
        _boolean(self.fault, "fault")


@dataclass(frozen=True, slots=True)
class AutonomicState:
    schema: str
    revision: int
    next_tick: int
    last_observation_tick: int | None
    arousal_milli: int
    rest_drive_milli: int
    rhythm_milli: int
    update_count: int
    hold_count: int
    stale_count: int
    fault_count: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != STATE_SCHEMA:
            raise HomeostasisError("unsupported autonomic state schema")
        _integer(self.revision, "state revision", 0, MAX_COUNTER)
        _integer(self.next_tick, "next_tick", 0, MAX_TICK)
        if self.last_observation_tick is not None:
            _integer(
                self.last_observation_tick,
                "last_observation_tick",
                0,
                MAX_TICK,
            )
            if self.last_observation_tick >= self.next_tick:
                raise HomeostasisError("last observation must precede next_tick")
        _integer(self.arousal_milli, "arousal_milli", 0, MAX_SIGNAL_MILLI)
        _integer(self.rest_drive_milli, "rest_drive_milli", 0, MAX_SIGNAL_MILLI)
        _integer(
            self.rhythm_milli,
            "rhythm_milli",
            -MAX_SIGNAL_MILLI,
            MAX_SIGNAL_MILLI,
        )
        for name in ("update_count", "hold_count", "stale_count", "fault_count"):
            _integer(getattr(self, name), name, 0, MAX_COUNTER)


@dataclass(frozen=True, slots=True)
class AutonomicResult:
    schema: str
    tick: int
    revision: int
    ran: bool
    status: str
    arousal_milli: int
    rest_drive_milli: int
    rhythm_milli: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != RESULT_SCHEMA:
            raise HomeostasisError("unsupported autonomic result schema")
        _integer(self.tick, "result tick", 0, MAX_TICK)
        _integer(self.revision, "result revision", 0, MAX_COUNTER)
        _boolean(self.ran, "ran")
        if type(self.status) is not str or self.status not in {
            "disabled",
            "fault",
            "stale",
            "cadence",
            "updated",
        }:
            raise HomeostasisError("unsupported autonomic result status")
        _integer(self.arousal_milli, "result arousal_milli", 0, MAX_SIGNAL_MILLI)
        _integer(
            self.rest_drive_milli,
            "result rest_drive_milli",
            0,
            MAX_SIGNAL_MILLI,
        )
        _integer(
            self.rhythm_milli,
            "result rhythm_milli",
            -MAX_SIGNAL_MILLI,
            MAX_SIGNAL_MILLI,
        )


def default_profile(*, enabled: bool = False) -> AutonomicProfile:
    """Return a detached conservative profile; execution is opt-in."""
    return AutonomicProfile(
        schema=PROFILE_SCHEMA,
        enabled=enabled,
        cadence_ticks=5,
        stale_after_ticks=5,
        rhythm_period_ticks=40,
        rhythm_amplitude_milli=80,
        base_arousal_milli=500,
        energy_target_milli=600,
        load_target_milli=400,
        energy_gain_milli=500,
        load_gain_milli=400,
        thermal_gain_milli=100,
        max_delta_milli=40,
    )


def initial_state(*, revision: int = 0, start_tick: int = 0) -> AutonomicState:
    return AutonomicState(
        schema=STATE_SCHEMA,
        revision=_integer(revision, "revision", 0, MAX_COUNTER),
        next_tick=_integer(start_tick, "start_tick", 0, MAX_TICK),
        last_observation_tick=None,
        arousal_milli=0,
        rest_drive_milli=MAX_SIGNAL_MILLI,
        rhythm_milli=0,
        update_count=0,
        hold_count=0,
        stale_count=0,
        fault_count=0,
    )


def _checked_profile(profile: object) -> AutonomicProfile:
    if type(profile) is not AutonomicProfile:
        raise HomeostasisError("an exact AutonomicProfile is required")
    return AutonomicProfile(**asdict(profile))


def _checked_state(state: object) -> AutonomicState:
    if type(state) is not AutonomicState:
        raise HomeostasisError("an exact AutonomicState is required")
    return AutonomicState(**asdict(state))


def _checked_observation(observation: object) -> AutonomicObservation:
    if type(observation) is not AutonomicObservation:
        raise HomeostasisError("an exact AutonomicObservation is required")
    return AutonomicObservation(**asdict(observation))


def profile_digest(profile: AutonomicProfile) -> str:
    checked = _checked_profile(profile)
    encoded = json.dumps(
        asdict(checked),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def rhythm_signal(profile: AutonomicProfile, tick: int) -> int:
    """Return an opt-in bounded triangular rhythm for one logical tick."""
    p = _checked_profile(profile)
    now = _integer(tick, "tick", 0, MAX_TICK)
    if not p.enabled:
        return 0
    phase = now % p.rhythm_period_ticks
    position_milli = 2 * phase * 1000 // p.rhythm_period_ticks
    triangle_milli = 1000 - 2 * abs(position_milli - 1000)
    return _divide_toward_zero(
        triangle_milli * p.rhythm_amplitude_milli,
        1000,
    )


def step(
    profile: AutonomicProfile,
    state: AutonomicState,
    observation: AutonomicObservation,
    *,
    tick: int,
) -> tuple[AutonomicState, AutonomicResult]:
    """Advance exactly one logical tick and emit bounded synthetic drives."""
    p = _checked_profile(profile)
    current = _checked_state(state)
    sample = _checked_observation(observation)
    if abs(current.rhythm_milli) > p.rhythm_amplitude_milli:
        raise HomeostasisError("state rhythm exceeds the profile bound")
    now = _integer(tick, "tick", 0, MAX_TICK - 1)
    if now != current.next_tick:
        raise HomeostasisError("tick must equal state.next_tick")
    if sample.revision != current.revision:
        raise HomeostasisError("stale autonomic revision")
    if sample.tick > now:
        raise HomeostasisError("autonomic observation is from the future")
    if (
        current.last_observation_tick is not None
        and sample.tick < current.last_observation_tick
    ):
        raise HomeostasisError("autonomic observation tick regressed")

    arousal = current.arousal_milli
    rest = current.rest_drive_milli
    rhythm = current.rhythm_milli
    update_count = current.update_count
    hold_count = current.hold_count
    stale_count = current.stale_count
    fault_count = current.fault_count
    ran = False

    if sample.fault:
        arousal, rest, rhythm = 0, MAX_SIGNAL_MILLI, 0
        fault_count = _increment(fault_count, "fault")
        status = "fault"
    elif now - sample.tick > p.stale_after_ticks:
        arousal, rest, rhythm = 0, MAX_SIGNAL_MILLI, 0
        stale_count = _increment(stale_count, "stale")
        status = "stale"
    elif not p.enabled:
        arousal, rest, rhythm = 0, MAX_SIGNAL_MILLI, 0
        hold_count = _increment(hold_count, "hold")
        status = "disabled"
    elif now % p.cadence_ticks:
        hold_count = _increment(hold_count, "hold")
        status = "cadence"
    else:
        rhythm = rhythm_signal(p, now)
        energy_term = _divide_toward_zero(
            (sample.energy_milli - p.energy_target_milli) * p.energy_gain_milli,
            1000,
        )
        overload = max(0, sample.load_milli - p.load_target_milli)
        load_term = _divide_toward_zero(overload * p.load_gain_milli, 1000)
        thermal_term = _divide_toward_zero(
            abs(sample.thermal_deviation_milli) * p.thermal_gain_milli,
            1000,
        )
        arousal_target = _clamp(
            p.base_arousal_milli + rhythm + energy_term - load_term - thermal_term,
            0,
            MAX_SIGNAL_MILLI,
        )
        rest_target = _clamp(
            MAX_SIGNAL_MILLI
            - arousal_target
            + max(0, -energy_term)
            + load_term
            + thermal_term,
            0,
            MAX_SIGNAL_MILLI,
        )
        arousal = _move_toward(
            current.arousal_milli,
            arousal_target,
            p.max_delta_milli,
        )
        rest = _move_toward(
            current.rest_drive_milli,
            rest_target,
            p.max_delta_milli,
        )
        update_count = _increment(update_count, "update")
        ran = True
        status = "updated"

    next_state = AutonomicState(
        schema=STATE_SCHEMA,
        revision=current.revision,
        next_tick=now + 1,
        last_observation_tick=sample.tick,
        arousal_milli=arousal,
        rest_drive_milli=rest,
        rhythm_milli=rhythm,
        update_count=update_count,
        hold_count=hold_count,
        stale_count=stale_count,
        fault_count=fault_count,
    )
    result = AutonomicResult(
        schema=RESULT_SCHEMA,
        tick=now,
        revision=current.revision,
        ran=ran,
        status=status,
        arousal_milli=arousal,
        rest_drive_milli=rest,
        rhythm_milli=rhythm,
    )
    return next_state, result


def snapshot(profile: AutonomicProfile, state: AutonomicState) -> dict[str, object]:
    """Return a detached JSON-compatible snapshot pinned to one profile."""
    checked_profile = _checked_profile(profile)
    checked = _checked_state(state)
    if abs(checked.rhythm_milli) > checked_profile.rhythm_amplitude_milli:
        raise HomeostasisError("state rhythm exceeds the profile bound")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "profile_sha256": profile_digest(checked_profile),
        "state": asdict(checked),
    }


def restore(
    profile: AutonomicProfile,
    payload: object,
    *,
    minimum_next_tick: int | None = None,
) -> AutonomicState:
    """Restore for replay; an optional floor prevents live clock rollback."""
    _checked_profile(profile)
    envelope = _exact_dict(
        payload,
        {"schema", "profile_sha256", "state"},
        "autonomic snapshot envelope",
    )
    if type(envelope["schema"]) is not str or envelope["schema"] != SNAPSHOT_SCHEMA:
        raise HomeostasisError("unsupported autonomic snapshot schema")
    digest = envelope["profile_sha256"]
    if type(digest) is not str or digest != profile_digest(profile):
        raise HomeostasisError("autonomic snapshot profile mismatch")
    raw_state = envelope["state"]
    fields = {
        "schema",
        "revision",
        "next_tick",
        "last_observation_tick",
        "arousal_milli",
        "rest_drive_milli",
        "rhythm_milli",
        "update_count",
        "hold_count",
        "stale_count",
        "fault_count",
    }
    checked_state = _exact_dict(raw_state, fields, "autonomic snapshot state")
    restored = AutonomicState(**checked_state)
    if abs(restored.rhythm_milli) > profile.rhythm_amplitude_milli:
        raise HomeostasisError("snapshot rhythm exceeds the profile bound")
    if minimum_next_tick is not None:
        floor = _integer(minimum_next_tick, "minimum_next_tick", 0, MAX_TICK)
        if restored.next_tick < floor:
            raise HomeostasisError("autonomic snapshot would roll back live time")
    return restored
