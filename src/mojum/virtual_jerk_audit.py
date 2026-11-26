"""Bounded offline jerk audit for synthetic virtual-joint trajectories.

This is a diagnostic over caller-supplied samples, not calibrated biomechanics,
physical sensing, or an actuator-safety mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Final

SCHEMA: Final = "mojum.body.joint-trajectory/1"
PUBLIC_SYNTHETIC: Final = "PUBLIC_SYNTHETIC"
MAX_SAMPLES: Final = 4096
MAX_POSITION_VALUES: Final = 65_536
MAX_JOINTS: Final = 256
MAX_NAME_LENGTH: Final = 64
MAX_STREAM_ID_LENGTH: Final = 64
MAX_TIMESTAMP_NS: Final = (1 << 63) - 1
MAX_ABS_POSITION: Final = 1_000_000.0
MAX_JERK_LIMIT: Final = 1_000_000_000_000_000.0


class VirtualJerkError(ValueError):
    """Raised when a synthetic trajectory cannot be admitted safely."""


@dataclass(frozen=True, slots=True)
class JerkViolation:
    joint: str
    sample_index: int
    jerk: float
    limit: float


@dataclass(frozen=True, slots=True)
class VirtualJerkReport:
    stream_id: str
    sample_count: int
    joint_count: int
    jerk_sample_count: int
    peak_abs_jerk: tuple[tuple[str, float], ...]
    violations: tuple[JerkViolation, ...]
    digest: str

    @property
    def passed(self) -> bool:
        """True only when every estimated jerk meets its declared limit."""
        return not self.violations


def _native_dict(value: object, expected: set[str], field: str) -> dict:
    if type(value) is not dict:
        raise VirtualJerkError(f"{field} must be a native dict")
    if len(value) != len(expected):
        raise VirtualJerkError(f"{field} has missing or unknown fields")
    max_key_length = max((len(key) for key in expected), default=0)
    if any(type(key) is not str or len(key) > max_key_length for key in dict.keys(value)):
        raise VirtualJerkError(f"{field} keys must be bounded native strings")
    if set(value) != expected:
        raise VirtualJerkError(f"{field} has missing or unknown fields")
    return value


def _finite_number(value: object, field: str, maximum: float) -> float:
    if type(value) not in (int, float):
        raise VirtualJerkError(f"{field} must be a native int or float")
    try:
        if abs(value) > maximum or not math.isfinite(value):
            raise VirtualJerkError(f"{field} is outside finite bounds")
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise VirtualJerkError(f"{field} is outside finite bounds") from None
    if not math.isfinite(result):
        raise VirtualJerkError(f"{field} must be finite")
    return result


def _native_text(value: object, field: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or not value.isascii():
        raise VirtualJerkError(f"{field} must be bounded nonempty ASCII text")
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise VirtualJerkError(f"{field} contains unsupported characters")
    return value


def audit_joint_jerk(trajectory: object, jerk_limits: object) -> VirtualJerkReport:
    """Estimate the third derivative of position on a nonuniform time grid.

    Each four-sample window uses a third divided difference on its actual
    timestamps. Six times that divided difference is the exact third
    derivative for cubic position trajectories, including nonuniform grids.
    Four samples are the minimum needed for one jerk estimate.

    The input has exactly schema, classification, consent, stream_id,
    joint_names and samples. Each sample has exactly time_ns and positions.
    Limits are caller-declared position-units per second cubed.
    """
    data = _native_dict(
        trajectory,
        {"schema", "classification", "consent", "stream_id", "joint_names", "samples"},
        "trajectory",
    )
    if type(data["schema"]) is not str or data["schema"] != SCHEMA:
        raise VirtualJerkError("unsupported trajectory schema")
    if type(data["classification"]) is not str or data["classification"] != PUBLIC_SYNTHETIC:
        raise VirtualJerkError("trajectory must be explicitly PUBLIC_SYNTHETIC")
    if type(data["consent"]) is not bool or data["consent"] is not True:
        raise VirtualJerkError("explicit synthetic-data consent is required")
    stream_id = _native_text(data["stream_id"], "stream_id", MAX_STREAM_ID_LENGTH)

    names = data["joint_names"]
    if type(names) is not list or not 1 <= len(names) <= MAX_JOINTS:
        raise VirtualJerkError("joint_names must be a bounded native list")
    clean_names = tuple(_native_text(name, "joint name", MAX_NAME_LENGTH) for name in names)
    if len(set(clean_names)) != len(clean_names):
        raise VirtualJerkError("joint_names must be unique")

    limits_data = _native_dict(jerk_limits, set(clean_names), "jerk_limits")
    limits = tuple(
        _finite_number(limits_data[name], f"limit for {name}", MAX_JERK_LIMIT)
        for name in clean_names
    )
    if any(limit <= 0.0 for limit in limits):
        raise VirtualJerkError("all jerk limits must be positive")

    samples = data["samples"]
    if type(samples) is not list or not 4 <= len(samples) <= MAX_SAMPLES:
        raise VirtualJerkError("samples must be a native list of 4..4096 frames")
    if len(samples) * len(clean_names) > MAX_POSITION_VALUES:
        raise VirtualJerkError("trajectory exceeds the total position-value budget")

    timestamps: list[int] = []
    positions: list[tuple[float, ...]] = []
    for index, raw_sample in enumerate(samples):
        sample = _native_dict(raw_sample, {"time_ns", "positions"}, f"samples[{index}]")
        timestamp = sample["time_ns"]
        if type(timestamp) is not int or not 0 <= timestamp <= MAX_TIMESTAMP_NS:
            raise VirtualJerkError(f"samples[{index}].time_ns must be a bounded native integer")
        if timestamps and timestamp <= timestamps[-1]:
            raise VirtualJerkError("sample timestamps must be strictly increasing")
        timestamps.append(timestamp)
        raw_positions = sample["positions"]
        if type(raw_positions) is not list or len(raw_positions) != len(clean_names):
            raise VirtualJerkError(f"samples[{index}].positions dimension mismatch")
        positions.append(tuple(
            _finite_number(value, f"samples[{index}].positions[{joint_index}]", MAX_ABS_POSITION)
            for joint_index, value in enumerate(raw_positions)
        ))

    # Keep time deltas as integers until each division. Converting absolute
    # timestamps to float seconds can collapse distinct nanosecond ticks when
    # a stream spans years, producing a zero denominator despite valid input.
    deltas_ns = [timestamps[index + 1] - timestamps[index]
                 for index in range(len(timestamps) - 1)]
    peaks = [0.0] * len(clean_names)
    violations: list[JerkViolation] = []
    for start_index in range(len(samples) - 3):
        end_index = start_index + 3
        for joint_index, joint in enumerate(clean_names):
            # Newton divided differences for four irregularly spaced samples.
            first = [
                (positions[start_index + offset + 1][joint_index]
                 - positions[start_index + offset][joint_index])
                * 1_000_000_000.0 / deltas_ns[start_index + offset]
                for offset in range(3)
            ]
            second = [
                (first[offset + 1] - first[offset]) * 1_000_000_000.0
                / (deltas_ns[start_index + offset] + deltas_ns[start_index + offset + 1])
                for offset in range(2)
            ]
            third = (second[1] - second[0]) * 1_000_000_000.0 / sum(
                deltas_ns[start_index:end_index]
            )
            jerk = 6.0 * third
            if not math.isfinite(jerk):
                raise VirtualJerkError("computed jerk is not finite")
            magnitude = abs(jerk)
            peaks[joint_index] = max(peaks[joint_index], magnitude)
            if magnitude > limits[joint_index]:
                violations.append(JerkViolation(joint, start_index + 1, jerk, limits[joint_index]))

    normalized = {
        "schema": SCHEMA,
        "classification": PUBLIC_SYNTHETIC,
        "consent": True,
        "stream_id": stream_id,
        "joint_names": list(clean_names),
        "timestamps_ns": timestamps,
        "positions": [list(frame) for frame in positions],
        "limits": {name: limits[index] for index, name in enumerate(clean_names)},
    }
    payload = {
        "input": normalized,
        "peaks": [[name, peaks[index]] for index, name in enumerate(clean_names)],
        "violations": [
            [item.joint, item.sample_index, item.jerk, item.limit] for item in violations
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    digest = hashlib.sha256(
        b"mojum.body.virtual-jerk-audit/1\0" + canonical
    ).hexdigest()

    return VirtualJerkReport(
        stream_id=stream_id,
        sample_count=len(samples),
        joint_count=len(clean_names),
        jerk_sample_count=len(samples) - 3,
        peak_abs_jerk=tuple((name, peaks[index]) for index, name in enumerate(clean_names)),
        violations=tuple(violations),
        digest=digest,
    )
