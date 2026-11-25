"""Bounded fixed-point bilateral oscillator for synthetic offline experiments.

This module produces dimensionless synthetic waveform samples.  It does not
produce joint targets, call MotorGate, integrate MuJoCo, or expose hardware
authority.  Physical standing, perturbation recovery and locomotion remain
separate measured acceptance work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


PROFILE_SCHEMA = "mojum.spinal-cpg.profile/1"
SNAPSHOT_SCHEMA = "mojum.spinal-cpg.snapshot/1"
FRAME_SCHEMA = "mojum.spinal-cpg.frame/1"

PHASE_MICROTURNS = 1_000_000
HALF_CYCLE = PHASE_MICROTURNS // 2
OUTPUT_MILLI = 1_000
MAX_TICKS = 1_000_000_000
MAX_REVISION = 2**63 - 1
MIN_TICK_US = 1_000
MAX_TICK_US = 100_000
MIN_FREQUENCY_MILLIHZ = 10
MAX_FREQUENCY_MILLIHZ = 5_000
MAX_PHASE_CORRECTION = 100_000
MAX_FREQUENCY_DELTA = MAX_FREQUENCY_MILLIHZ
_FREQUENCY_REMAINDER_DIVISOR = 1_000


class CPGError(ValueError):
    """An untrusted profile, feedback record or snapshot is invalid."""


def _integer(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CPGError(f"{label} must be a native integer in [{low}, {high}]")
    return value


def _clamp(value: int, low: int, high: int) -> int:
    return min(high, max(low, value))


def _scaled_toward_zero(value: int, numerator: int, denominator: int) -> int:
    magnitude = abs(value) * numerator // denominator
    return -magnitude if value < 0 else magnitude


def _signed_phase_error(left: int, right: int) -> int:
    """Return the shortest signed error from the desired half-cycle offset."""
    error = (right - left - HALF_CYCLE) % PHASE_MICROTURNS
    if error >= HALF_CYCLE:
        error -= PHASE_MICROTURNS
    return error


def _triangle_milli(phase: int) -> int:
    """Exact triangle wave in [-1000, 1000] for one admitted phase."""
    quarter = PHASE_MICROTURNS // 4
    if phase < quarter:
        return phase * OUTPUT_MILLI // quarter
    if phase < 2 * quarter:
        return OUTPUT_MILLI - (phase - quarter) * OUTPUT_MILLI // quarter
    if phase < 3 * quarter:
        return -((phase - 2 * quarter) * OUTPUT_MILLI // quarter)
    return -OUTPUT_MILLI + (phase - 3 * quarter) * OUTPUT_MILLI // quarter


@dataclass(frozen=True, slots=True)
class CPGProfile:
    """Immutable limits and gains for one synthetic oscillator revision."""

    revision: int
    tick_us: int
    minimum_frequency_millihz: int
    base_frequency_millihz: int
    maximum_frequency_millihz: int
    amplitude_milli: int
    coupling_gain_milli: int
    sensory_phase_gain: int
    maximum_phase_correction: int

    def __post_init__(self) -> None:
        _integer(self.revision, 0, MAX_REVISION, "revision")
        _integer(self.tick_us, MIN_TICK_US, MAX_TICK_US, "tick_us")
        minimum = _integer(
            self.minimum_frequency_millihz,
            MIN_FREQUENCY_MILLIHZ,
            MAX_FREQUENCY_MILLIHZ,
            "minimum_frequency_millihz",
        )
        base = _integer(
            self.base_frequency_millihz,
            MIN_FREQUENCY_MILLIHZ,
            MAX_FREQUENCY_MILLIHZ,
            "base_frequency_millihz",
        )
        maximum = _integer(
            self.maximum_frequency_millihz,
            MIN_FREQUENCY_MILLIHZ,
            MAX_FREQUENCY_MILLIHZ,
            "maximum_frequency_millihz",
        )
        if not minimum <= base <= maximum:
            raise CPGError("frequency bounds must contain the base frequency")
        _integer(self.amplitude_milli, 1, OUTPUT_MILLI, "amplitude_milli")
        _integer(self.coupling_gain_milli, 1, OUTPUT_MILLI, "coupling_gain_milli")
        sensory = _integer(
            self.sensory_phase_gain,
            0,
            MAX_PHASE_CORRECTION,
            "sensory_phase_gain",
        )
        correction = _integer(
            self.maximum_phase_correction,
            1,
            MAX_PHASE_CORRECTION,
            "maximum_phase_correction",
        )
        if sensory > correction:
            raise CPGError("sensory phase gain exceeds the correction bound")
        maximum_advance = maximum * self.tick_us // _FREQUENCY_REMAINDER_DIVISOR
        maximum_applied_correction = min(correction, maximum_advance // 2)
        if maximum_advance + maximum_applied_correction > HALF_CYCLE:
            raise CPGError(
                "profile can advance one side more than half a cycle per tick"
            )

    @property
    def sha256(self) -> str:
        return _profile_digest(self)


@dataclass(frozen=True, slots=True)
class SensoryFeedback:
    """One explicitly synthetic, tick-bound modulation record."""

    tick: int
    profile_revision: int
    frequency_delta_millihz: int = 0
    left_load_milli: int = 0
    right_load_milli: int = 0
    synthetic: bool = True

    def __post_init__(self) -> None:
        _integer(self.tick, 1, MAX_TICKS, "feedback tick")
        _integer(self.profile_revision, 0, MAX_REVISION, "feedback profile revision")
        _integer(
            self.frequency_delta_millihz,
            -MAX_FREQUENCY_DELTA,
            MAX_FREQUENCY_DELTA,
            "frequency delta",
        )
        _integer(self.left_load_milli, 0, OUTPUT_MILLI, "left load")
        _integer(self.right_load_milli, 0, OUTPUT_MILLI, "right load")
        if self.synthetic is not True:
            raise CPGError("feedback must be explicitly synthetic")


@dataclass(frozen=True, slots=True)
class CPGSnapshot:
    """Immutable serialized state; validation occurs at the restore boundary."""

    schema: str
    profile_revision: int
    profile_sha256: str
    tick: int
    left_phase_microturns: int
    right_phase_microturns: int
    frequency_remainder: int
    state_sha256: str


@dataclass(frozen=True, slots=True)
class CPGFrame:
    """One bounded synthetic output frame with no actuation authority."""

    schema: str
    tick: int
    profile_revision: int
    profile_sha256: str
    frequency_millihz: int
    left_phase_microturns: int
    right_phase_microturns: int
    left_output_milli: int
    right_output_milli: int
    coupling_error_before_microturns: int
    phase_correction_microturns: int
    state_sha256: str
    waveform: str
    physical_actuation: bool


def _profile_payload(profile: CPGProfile) -> dict[str, int | str]:
    return {
        "schema": PROFILE_SCHEMA,
        "revision": profile.revision,
        "tick_us": profile.tick_us,
        "minimum_frequency_millihz": profile.minimum_frequency_millihz,
        "base_frequency_millihz": profile.base_frequency_millihz,
        "maximum_frequency_millihz": profile.maximum_frequency_millihz,
        "amplitude_milli": profile.amplitude_milli,
        "coupling_gain_milli": profile.coupling_gain_milli,
        "sensory_phase_gain": profile.sensory_phase_gain,
        "maximum_phase_correction": profile.maximum_phase_correction,
    }


def _canonical_digest(domain: str, payload: dict[str, object]) -> str:
    wire = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + wire).hexdigest()


def _profile_digest(profile: CPGProfile) -> str:
    return _canonical_digest(PROFILE_SCHEMA, _profile_payload(profile))


def _snapshot_payload(
    profile_revision: int,
    profile_sha256: str,
    tick: int,
    left_phase: int,
    right_phase: int,
    remainder: int,
) -> dict[str, int | str]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "profile_revision": profile_revision,
        "profile_sha256": profile_sha256,
        "tick": tick,
        "left_phase_microturns": left_phase,
        "right_phase_microturns": right_phase,
        "frequency_remainder": remainder,
    }


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class BilateralCPG:
    """Stateful deterministic bilateral oscillator with bounded modulation."""

    __slots__ = ("_profile", "_tick", "_left_phase", "_right_phase", "_remainder")

    def __init__(self, profile: CPGProfile):
        if type(profile) is not CPGProfile:
            raise CPGError("an exact CPGProfile is required")
        self._profile = profile
        self._tick = 0
        self._left_phase = 0
        self._right_phase = HALF_CYCLE
        self._remainder = 0

    @property
    def profile(self) -> CPGProfile:
        return self._profile

    @property
    def tick(self) -> int:
        return self._tick

    def snapshot(self) -> CPGSnapshot:
        profile_sha256 = self._profile.sha256
        payload = _snapshot_payload(
            self._profile.revision,
            profile_sha256,
            self._tick,
            self._left_phase,
            self._right_phase,
            self._remainder,
        )
        return CPGSnapshot(
            **payload,
            state_sha256=_canonical_digest(SNAPSHOT_SCHEMA, payload),
        )

    @classmethod
    def from_snapshot(cls, profile: CPGProfile, snapshot: CPGSnapshot) -> "BilateralCPG":
        if type(profile) is not CPGProfile or type(snapshot) is not CPGSnapshot:
            raise CPGError("exact profile and snapshot types are required")
        if snapshot.schema != SNAPSHOT_SCHEMA:
            raise CPGError("unknown CPG snapshot schema")
        _integer(snapshot.profile_revision, 0, MAX_REVISION, "snapshot profile revision")
        _integer(snapshot.tick, 0, MAX_TICKS, "snapshot tick")
        _integer(
            snapshot.left_phase_microturns,
            0,
            PHASE_MICROTURNS - 1,
            "left snapshot phase",
        )
        _integer(
            snapshot.right_phase_microturns,
            0,
            PHASE_MICROTURNS - 1,
            "right snapshot phase",
        )
        _integer(
            snapshot.frequency_remainder,
            0,
            _FREQUENCY_REMAINDER_DIVISOR - 1,
            "snapshot frequency remainder",
        )
        if not _valid_sha256(snapshot.profile_sha256) or not _valid_sha256(
            snapshot.state_sha256
        ):
            raise CPGError("snapshot digest must be lowercase SHA-256")
        if (
            snapshot.profile_revision != profile.revision
            or snapshot.profile_sha256 != profile.sha256
        ):
            raise CPGError("snapshot is bound to a different profile")
        payload = _snapshot_payload(
            snapshot.profile_revision,
            snapshot.profile_sha256,
            snapshot.tick,
            snapshot.left_phase_microturns,
            snapshot.right_phase_microturns,
            snapshot.frequency_remainder,
        )
        if snapshot.state_sha256 != _canonical_digest(SNAPSHOT_SCHEMA, payload):
            raise CPGError("snapshot state digest mismatch")
        restored = cls(profile)
        restored._tick = snapshot.tick
        restored._left_phase = snapshot.left_phase_microturns
        restored._right_phase = snapshot.right_phase_microturns
        restored._remainder = snapshot.frequency_remainder
        return restored

    def step(self, feedback: SensoryFeedback) -> CPGFrame:
        if type(feedback) is not SensoryFeedback:
            raise CPGError("an exact SensoryFeedback record is required")
        if self._tick >= MAX_TICKS:
            raise CPGError("CPG lifetime tick budget exhausted")
        next_tick = self._tick + 1
        if feedback.tick != next_tick:
            raise CPGError("feedback tick must be the next contiguous CPG tick")
        if feedback.profile_revision != self._profile.revision:
            raise CPGError("feedback profile revision mismatch")

        frequency = _clamp(
            self._profile.base_frequency_millihz + feedback.frequency_delta_millihz,
            self._profile.minimum_frequency_millihz,
            self._profile.maximum_frequency_millihz,
        )
        numerator = frequency * self._profile.tick_us + self._remainder
        advance, remainder = divmod(numerator, _FREQUENCY_REMAINDER_DIVISOR)

        error = _signed_phase_error(self._left_phase, self._right_phase)
        coupling = _scaled_toward_zero(
            error, self._profile.coupling_gain_milli, OUTPUT_MILLI
        )
        load_error = feedback.right_load_milli - feedback.left_load_milli
        sensory = _scaled_toward_zero(
            load_error, self._profile.sensory_phase_gain, OUTPUT_MILLI
        )
        available = min(self._profile.maximum_phase_correction, advance // 2)
        correction = _clamp(coupling + sensory, -available, available)

        self._left_phase = (self._left_phase + advance + correction) % PHASE_MICROTURNS
        self._right_phase = (self._right_phase + advance - correction) % PHASE_MICROTURNS
        self._tick = next_tick
        self._remainder = remainder

        left_output = _scaled_toward_zero(
            _triangle_milli(self._left_phase),
            self._profile.amplitude_milli,
            OUTPUT_MILLI,
        )
        right_output = _scaled_toward_zero(
            _triangle_milli(self._right_phase),
            self._profile.amplitude_milli,
            OUTPUT_MILLI,
        )
        state = self.snapshot()
        return CPGFrame(
            schema=FRAME_SCHEMA,
            tick=next_tick,
            profile_revision=self._profile.revision,
            profile_sha256=self._profile.sha256,
            frequency_millihz=frequency,
            left_phase_microturns=self._left_phase,
            right_phase_microturns=self._right_phase,
            left_output_milli=left_output,
            right_output_milli=right_output,
            coupling_error_before_microturns=error,
            phase_correction_microturns=correction,
            state_sha256=state.state_sha256,
            waveform="FIXED_POINT_TRIANGLE",
            physical_actuation=False,
        )
