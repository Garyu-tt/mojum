"""Deterministic, CPU-only posture-coordination advisory contract.

The values in this module are synthetic fixed-point control signals.  They are
not a biological cerebellum, a calibrated posture model, a motor driver, or an
actuation safety boundary.  Callers must pass any resulting target through
their existing revisioned motor-safety contract before using it elsewhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Final


PROFILE_SCHEMA: Final = "mojum.mikoshi.coordination-profile/1"
OBSERVATION_SCHEMA: Final = "mojum.mikoshi.coordination-observation/1"
STATE_SCHEMA: Final = "mojum.mikoshi.coordination-state/1"
RESULT_SCHEMA: Final = "mojum.mikoshi.coordination-result/1"
SNAPSHOT_SCHEMA: Final = "mojum.mikoshi.coordination-snapshot/1"
CLASSIFICATION: Final = "PUBLIC_SYNTHETIC"

MAX_TICK: Final = 2**63 - 1
MAX_COUNTER: Final = 2**63 - 1
MAX_CADENCE_TICKS: Final = 1_000_000
MAX_ERROR_MILLI: Final = 1_000_000
MAX_GAIN_MILLI: Final = 10_000
MAX_CORRECTION_MILLI: Final = 1_000_000


class CoordinationError(ValueError):
    """Invalid profile, observation, state, or transition."""


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CoordinationError(
            f"{name} must be a native integer in [{minimum}, {maximum}]"
        )
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise CoordinationError(f"{name} must be a native bool")
    return value


def _increment(value: int, name: str) -> int:
    if value == MAX_COUNTER:
        raise CoordinationError(f"{name} counter overflow")
    return value + 1


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value))


def _divide_toward_zero(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def _exact_dict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if type(value) is not dict or len(value) != len(fields):
        raise CoordinationError(f"invalid {name}")
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > 32 for key in keys):
        raise CoordinationError(f"invalid {name} keys")
    if set(keys) != fields:
        raise CoordinationError(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True)
class CoordinationProfile:
    schema: str
    enabled: bool
    cadence_ticks: int
    stale_after_ticks: int
    posture_gain_milli: int
    velocity_gain_milli: int
    support_gain_milli: int
    deadband_milli: int
    max_correction_milli: int
    max_delta_milli: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != PROFILE_SCHEMA:
            raise CoordinationError("unsupported coordination profile schema")
        _boolean(self.enabled, "enabled")
        _integer(self.cadence_ticks, "cadence_ticks", 1, MAX_CADENCE_TICKS)
        _integer(
            self.stale_after_ticks,
            "stale_after_ticks",
            0,
            MAX_CADENCE_TICKS,
        )
        gains = (
            _integer(
                self.posture_gain_milli,
                "posture_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
            _integer(
                self.velocity_gain_milli,
                "velocity_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
            _integer(
                self.support_gain_milli,
                "support_gain_milli",
                0,
                MAX_GAIN_MILLI,
            ),
        )
        if not any(gains):
            raise CoordinationError("at least one coordination gain must be nonzero")
        _integer(
            self.max_correction_milli,
            "max_correction_milli",
            1,
            MAX_CORRECTION_MILLI,
        )
        _integer(
            self.deadband_milli,
            "deadband_milli",
            0,
            self.max_correction_milli,
        )
        _integer(
            self.max_delta_milli,
            "max_delta_milli",
            1,
            self.max_correction_milli,
        )


@dataclass(frozen=True, slots=True)
class CoordinationObservation:
    schema: str
    classification: str
    tick: int
    revision: int
    posture_error_milli: int
    velocity_error_milli: int
    support_error_milli: int
    fault: bool = False

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != OBSERVATION_SCHEMA:
            raise CoordinationError("unsupported coordination observation schema")
        if type(self.classification) is not str or self.classification != CLASSIFICATION:
            raise CoordinationError("coordination input must be PUBLIC_SYNTHETIC")
        _integer(self.tick, "observation tick", 0, MAX_TICK)
        _integer(self.revision, "observation revision", 0, MAX_COUNTER)
        for name in (
            "posture_error_milli",
            "velocity_error_milli",
            "support_error_milli",
        ):
            _integer(getattr(self, name), name, -MAX_ERROR_MILLI, MAX_ERROR_MILLI)
        _boolean(self.fault, "fault")


@dataclass(frozen=True, slots=True)
class CoordinationState:
    schema: str
    revision: int
    next_tick: int
    last_observation_tick: int | None
    correction_milli: int
    update_count: int
    hold_count: int
    stale_count: int
    fault_count: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != STATE_SCHEMA:
            raise CoordinationError("unsupported coordination state schema")
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
                raise CoordinationError("last observation must precede next_tick")
        _integer(
            self.correction_milli,
            "correction_milli",
            -MAX_CORRECTION_MILLI,
            MAX_CORRECTION_MILLI,
        )
        for name in ("update_count", "hold_count", "stale_count", "fault_count"):
            _integer(getattr(self, name), name, 0, MAX_COUNTER)


@dataclass(frozen=True, slots=True)
class CoordinationResult:
    schema: str
    tick: int
    revision: int
    ran: bool
    status: str
    correction_milli: int

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != RESULT_SCHEMA:
            raise CoordinationError("unsupported coordination result schema")
        _integer(self.tick, "result tick", 0, MAX_TICK)
        _integer(self.revision, "result revision", 0, MAX_COUNTER)
        _boolean(self.ran, "ran")
        if type(self.status) is not str or self.status not in {
            "disabled",
            "fault",
            "stale",
            "cadence",
            "deadband",
            "updated",
        }:
            raise CoordinationError("unsupported coordination result status")
        _integer(
            self.correction_milli,
            "result correction_milli",
            -MAX_CORRECTION_MILLI,
            MAX_CORRECTION_MILLI,
        )


def default_profile(*, enabled: bool = False) -> CoordinationProfile:
    """Return a detached conservative profile; execution is opt-in."""
    return CoordinationProfile(
        schema=PROFILE_SCHEMA,
        enabled=enabled,
        cadence_ticks=2,
        stale_after_ticks=2,
        posture_gain_milli=700,
        velocity_gain_milli=200,
        support_gain_milli=100,
        deadband_milli=5,
        max_correction_milli=500,
        max_delta_milli=50,
    )


def initial_state(*, revision: int = 0, start_tick: int = 0) -> CoordinationState:
    return CoordinationState(
        schema=STATE_SCHEMA,
        revision=_integer(revision, "revision", 0, MAX_COUNTER),
        next_tick=_integer(start_tick, "start_tick", 0, MAX_TICK),
        last_observation_tick=None,
        correction_milli=0,
        update_count=0,
        hold_count=0,
        stale_count=0,
        fault_count=0,
    )


def _checked_profile(profile: object) -> CoordinationProfile:
    if type(profile) is not CoordinationProfile:
        raise CoordinationError("an exact CoordinationProfile is required")
    return CoordinationProfile(**asdict(profile))


def _checked_state(state: object) -> CoordinationState:
    if type(state) is not CoordinationState:
        raise CoordinationError("an exact CoordinationState is required")
    return CoordinationState(**asdict(state))


def _checked_observation(observation: object) -> CoordinationObservation:
    if type(observation) is not CoordinationObservation:
        raise CoordinationError("an exact CoordinationObservation is required")
    return CoordinationObservation(**asdict(observation))


def profile_digest(profile: CoordinationProfile) -> str:
    checked = _checked_profile(profile)
    encoded = json.dumps(
        asdict(checked),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def step(
    profile: CoordinationProfile,
    state: CoordinationState,
    observation: CoordinationObservation,
    *,
    tick: int,
) -> tuple[CoordinationState, CoordinationResult]:
    """Advance exactly one logical tick and emit a bounded advisory signal."""
    p = _checked_profile(profile)
    current = _checked_state(state)
    sample = _checked_observation(observation)
    if abs(current.correction_milli) > p.max_correction_milli:
        raise CoordinationError("state correction exceeds the profile bound")
    now = _integer(tick, "tick", 0, MAX_TICK - 1)
    if now != current.next_tick:
        raise CoordinationError("tick must equal state.next_tick")
    if sample.revision != current.revision:
        raise CoordinationError("stale coordination revision")
    if sample.tick > now:
        raise CoordinationError("coordination observation is from the future")
    if (
        current.last_observation_tick is not None
        and sample.tick < current.last_observation_tick
    ):
        raise CoordinationError("coordination observation tick regressed")

    correction = current.correction_milli
    update_count = current.update_count
    hold_count = current.hold_count
    stale_count = current.stale_count
    fault_count = current.fault_count
    ran = False

    if sample.fault:
        correction = 0
        fault_count = _increment(fault_count, "fault")
        status = "fault"
    elif now - sample.tick > p.stale_after_ticks:
        correction = 0
        stale_count = _increment(stale_count, "stale")
        status = "stale"
    elif not p.enabled:
        correction = 0
        hold_count = _increment(hold_count, "hold")
        status = "disabled"
    elif now % p.cadence_ticks:
        hold_count = _increment(hold_count, "hold")
        status = "cadence"
    else:
        weighted_error = (
            sample.posture_error_milli * p.posture_gain_milli
            + sample.velocity_error_milli * p.velocity_gain_milli
            + sample.support_error_milli * p.support_gain_milli
        )
        target = -_divide_toward_zero(weighted_error, 1000)
        target = _clamp(
            target,
            -p.max_correction_milli,
            p.max_correction_milli,
        )
        if abs(target) <= p.deadband_milli:
            target = 0
            status = "deadband"
        else:
            status = "updated"
        correction = _clamp(
            target,
            current.correction_milli - p.max_delta_milli,
            current.correction_milli + p.max_delta_milli,
        )
        update_count = _increment(update_count, "update")
        ran = True

    next_state = CoordinationState(
        schema=STATE_SCHEMA,
        revision=current.revision,
        next_tick=now + 1,
        last_observation_tick=sample.tick,
        correction_milli=correction,
        update_count=update_count,
        hold_count=hold_count,
        stale_count=stale_count,
        fault_count=fault_count,
    )
    result = CoordinationResult(
        schema=RESULT_SCHEMA,
        tick=now,
        revision=current.revision,
        ran=ran,
        status=status,
        correction_milli=correction,
    )
    return next_state, result


def snapshot(
    profile: CoordinationProfile,
    state: CoordinationState,
) -> dict[str, object]:
    """Return a detached JSON-compatible snapshot pinned to one profile."""
    checked_profile = _checked_profile(profile)
    checked = _checked_state(state)
    if abs(checked.correction_milli) > checked_profile.max_correction_milli:
        raise CoordinationError("state correction exceeds the profile bound")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "profile_sha256": profile_digest(checked_profile),
        "state": asdict(checked),
    }


def restore(
    profile: CoordinationProfile,
    payload: object,
    *,
    minimum_next_tick: int | None = None,
) -> CoordinationState:
    """Restore for replay; an optional floor prevents live clock rollback."""
    _checked_profile(profile)
    envelope = _exact_dict(
        payload,
        {"schema", "profile_sha256", "state"},
        "coordination snapshot envelope",
    )
    if type(envelope["schema"]) is not str or envelope["schema"] != SNAPSHOT_SCHEMA:
        raise CoordinationError("unsupported coordination snapshot schema")
    digest = envelope["profile_sha256"]
    if type(digest) is not str or digest != profile_digest(profile):
        raise CoordinationError("coordination snapshot profile mismatch")
    raw_state = envelope["state"]
    fields = {
        "schema",
        "revision",
        "next_tick",
        "last_observation_tick",
        "correction_milli",
        "update_count",
        "hold_count",
        "stale_count",
        "fault_count",
    }
    checked_state = _exact_dict(raw_state, fields, "coordination snapshot state")
    restored = CoordinationState(**checked_state)
    if abs(restored.correction_milli) > profile.max_correction_milli:
        raise CoordinationError("snapshot correction exceeds the profile bound")
    if minimum_next_tick is not None:
        floor = _integer(minimum_next_tick, "minimum_next_tick", 0, MAX_TICK)
        if restored.next_tick < floor:
            raise CoordinationError("coordination snapshot would roll back live time")
    return restored
