"""Bounded offline audit of synthetic virtual-joint jerk.

Diagnostic only: no motor commands, sensors, physics engine, or calibrated
biomechanical safety claims.
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


class BodyJerkError(ValueError):
    """Raised when a synthetic trajectory cannot be admitted safely."""


@dataclass(frozen=True, slots=True)
class JerkViolation:
    joint: str
    window_start_index: int
    jerk: float
    limit: float


@dataclass(frozen=True, slots=True)
class BodyJerkReport:
    stream_id: str
    sample_count: int
    joint_count: int
    window_count: int
    peak_abs_jerk: tuple[tuple[str, float], ...]
    violations: tuple[JerkViolation, ...]
    digest: str

    @property
    def passed(self) -> bool:
        """True only when every measured window meets its declared bound."""
        return not self.violations


def _native_dict(value: object, expected: set[str], field: str) -> dict:
    if type(value) is not dict:
        raise BodyJerkError(f"{field} must be a native dict")
    if len(value) != len(expected):
        raise BodyJerkError(f"{field} has missing or unknown fields")
    if any(type(key) is not str for key in value):
        raise BodyJerkError(f"{field} keys must be native strings")
    if set(value) != expected:
        raise BodyJerkError(f"{field} has missing or unknown fields")
    return value


def _finite_number(value: object, field: str, maximum: float) -> float:
    if type(value) not in (int, float):
        raise BodyJerkError(f"{field} must be a native int or float")
    try:
        if abs(value) > maximum or not math.isfinite(value):
            raise BodyJerkError(f"{field} is outside finite bounds")
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise BodyJerkError(f"{field} is outside finite bounds") from None
    if not math.isfinite(result):
        raise BodyJerkError(f"{field} must be finite")
    return result


def _native_text(value: object, field: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or not value.isascii():
        raise BodyJerkError(f"{field} must be bounded nonempty ASCII text")
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise BodyJerkError(f"{field} contains unsupported characters")
    return value


def audit_joint_jerk(trajectory: object, jerk_limits: object) -> BodyJerkReport:
    """Audit third divided differences for PUBLIC_SYNTHETIC joint positions.

    The trajectory has exactly: schema, classification, consent, stream_id,
    joint_names, samples. Each sample has exactly time_ns and positions.
    Four consecutive samples form one window. With timestamps converted to
    seconds, recursively form first, second and third divided differences;
    the estimate is six times the third divided difference. This is exact for
    cubic position trajectories even with nonuniform sample spacing.

    Limits use position-units per second cubed.
    """
    data = _native_dict(
        trajectory,
        {"schema", "classification", "consent", "stream_id", "joint_names", "samples"},
        "trajectory",
    )
    if type(data["schema"]) is not str or data["schema"] != SCHEMA:
        raise BodyJerkError("unsupported trajectory schema")
    if type(data["classification"]) is not str or data["classification"] != PUBLIC_SYNTHETIC:
        raise BodyJerkError("trajectory must be explicitly PUBLIC_SYNTHETIC")
    if type(data["consent"]) is not bool or data["consent"] is not True:
        raise BodyJerkError("explicit synthetic-data consent is required")
    stream_id = _native_text(data["stream_id"], "stream_id", MAX_STREAM_ID_LENGTH)

    names = data["joint_names"]
    if type(names) is not list or not 1 <= len(names) <= MAX_JOINTS:
        raise BodyJerkError("joint_names must be a bounded native list")
    clean_names = tuple(_native_text(name, "joint name", MAX_NAME_LENGTH) for name in names)
    if len(set(clean_names)) != len(clean_names):
        raise BodyJerkError("joint_names must be unique")

    limits_data = _native_dict(jerk_limits, set(clean_names), "jerk_limits")
    limits = tuple(
        _finite_number(limits_data[name], f"limit for {name}", MAX_JERK_LIMIT)
        for name in clean_names
    )
    if any(limit <= 0.0 for limit in limits):
        raise BodyJerkError("all jerk limits must be positive")

    samples = data["samples"]
    if type(samples) is not list or not 4 <= len(samples) <= MAX_SAMPLES:
        raise BodyJerkError("samples must be a native list of 4..4096 frames")
    if len(samples) * len(clean_names) > MAX_POSITION_VALUES:
        raise BodyJerkError("trajectory exceeds the total position-value budget")

    timestamps: list[int] = []
    positions: list[tuple[float, ...]] = []
    for index, raw_sample in enumerate(samples):
        sample = _native_dict(raw_sample, {"time_ns", "positions"}, f"samples[{index}]")
        timestamp = sample["time_ns"]
        if type(timestamp) is not int or not 0 <= timestamp <= MAX_TIMESTAMP_NS:
            raise BodyJerkError(f"samples[{index}].time_ns must be a bounded native integer")
        if timestamps and timestamp <= timestamps[-1]:
            raise BodyJerkError("sample timestamps must be strictly increasing")
        timestamps.append(timestamp)
        raw_positions = sample["positions"]
        if type(raw_positions) is not list or len(raw_positions) != len(clean_names):
            raise BodyJerkError(f"samples[{index}].positions dimension mismatch")
        positions.append(tuple(
            _finite_number(value, f"samples[{index}].positions[{joint_index}]", MAX_ABS_POSITION)
            for joint_index, value in enumerate(raw_positions)
        ))

    peaks = [0.0] * len(clean_names)
    violations: list[JerkViolation] = []
    for start in range(len(samples) - 3):
        times = tuple(timestamps[start + offset] / 1_000_000_000.0 for offset in range(4))
        intervals = (times[1] - times[0], times[2] - times[1], times[3] - times[2])
        spans = (times[2] - times[0], times[3] - times[1], times[3] - times[0])
        if any(not math.isfinite(value) or value <= 0.0 for value in (*intervals, *spans)):
            raise BodyJerkError("computed time interval is invalid")
        for joint_index, joint in enumerate(clean_names):
            q0, q1, q2, q3 = (
                positions[start + offset][joint_index] for offset in range(4)
            )
            d1_0 = (q1 - q0) / intervals[0]
            d1_1 = (q2 - q1) / intervals[1]
            d1_2 = (q3 - q2) / intervals[2]
            d2_0 = (d1_1 - d1_0) / spans[0]
            d2_1 = (d1_2 - d1_1) / spans[1]
            jerk = 6.0 * (d2_1 - d2_0) / spans[2]
            if not math.isfinite(jerk):
                raise BodyJerkError("computed jerk is not finite")
            magnitude = abs(jerk)
            peaks[joint_index] = max(peaks[joint_index], magnitude)
            if magnitude > limits[joint_index]:
                violations.append(JerkViolation(joint, start, jerk, limits[joint_index]))

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
    digest_payload = {
        "input": normalized,
        "peaks": [[name, peaks[index]] for index, name in enumerate(clean_names)],
        "violations": [
            [item.joint, item.window_start_index, item.jerk, item.limit]
            for item in violations
        ],
    }
    canonical = json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    digest = hashlib.sha256(
        b"mojum.body.jerk-audit/1" + bytes((0,)) + canonical
    ).hexdigest()

    return BodyJerkReport(
        stream_id=stream_id,
        sample_count=len(samples),
        joint_count=len(clean_names),
        window_count=len(samples) - 3,
        peak_abs_jerk=tuple((name, peaks[index]) for index, name in enumerate(clean_names)),
        violations=tuple(violations),
        digest=digest,
    )
