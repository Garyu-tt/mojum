"""Bounded offline audit of synthetic virtual-joint acceleration.

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
MAX_ACCELERATION_LIMIT: Final = 1_000_000_000_000.0


class BodyAccelerationError(ValueError):
    """Raised when a synthetic trajectory cannot be admitted safely."""


@dataclass(frozen=True, slots=True)
class AccelerationViolation:
    joint: str
    middle_sample_index: int
    acceleration: float
    limit: float


@dataclass(frozen=True, slots=True)
class BodyAccelerationReport:
    stream_id: str
    sample_count: int
    joint_count: int
    interval_count: int
    peak_abs_acceleration: tuple[tuple[str, float], ...]
    violations: tuple[AccelerationViolation, ...]
    digest: str

    @property
    def passed(self) -> bool:
        """True only when every measured acceleration meets its declared bound."""
        return not self.violations


def _native_dict(value: object, expected: set[str], field: str) -> dict:
    if type(value) is not dict:
        raise BodyAccelerationError(f"{field} must be a native dict")
    # The exact key count is known (at most 256 for limits); reject huge
    # mappings before scanning their keys or constructing a set snapshot.
    if len(value) != len(expected):
        raise BodyAccelerationError(f"{field} has missing or unknown fields")
    # Check keys before equality/set operations to reject hostile key subclasses.
    if any(type(key) is not str for key in value):
        raise BodyAccelerationError(f"{field} keys must be native strings")
    if set(value) != expected:
        raise BodyAccelerationError(f"{field} has missing or unknown fields")
    return value


def _finite_number(value: object, field: str, maximum: float) -> float:
    if type(value) not in (int, float):
        raise BodyAccelerationError(f"{field} must be a native int or float")
    try:
        if abs(value) > maximum or not math.isfinite(value):
            raise BodyAccelerationError(f"{field} is outside finite bounds")
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise BodyAccelerationError(f"{field} is outside finite bounds") from None
    if not math.isfinite(result):
        raise BodyAccelerationError(f"{field} must be finite")
    return result


def _native_text(value: object, field: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or not value.isascii():
        raise BodyAccelerationError(f"{field} must be bounded nonempty ASCII text")
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise BodyAccelerationError(f"{field} contains unsupported characters")
    return value


def audit_joint_acceleration(trajectory: object, acceleration_limits: object) -> BodyAccelerationReport:
    """Audit discrete second derivatives for a PUBLIC_SYNTHETIC joint trajectory.

    The trajectory has exactly: schema, classification, consent, stream_id,
    joint_names, samples. Each sample has exactly time_ns and positions.
    Timestamps are strictly increasing integer nanoseconds. At each middle
    sample, nonuniform three-point acceleration is:

      2 * ((q[i+1]-q[i])/dt_right - (q[i]-q[i-1])/dt_left)
        / (dt_left + dt_right)

    Limits use position-units per second squared.
    """
    data = _native_dict(
        trajectory,
        {"schema", "classification", "consent", "stream_id", "joint_names", "samples"},
        "trajectory",
    )
    if type(data["schema"]) is not str or data["schema"] != SCHEMA:
        raise BodyAccelerationError("unsupported trajectory schema")
    if type(data["classification"]) is not str or data["classification"] != PUBLIC_SYNTHETIC:
        raise BodyAccelerationError("trajectory must be explicitly PUBLIC_SYNTHETIC")
    if type(data["consent"]) is not bool or data["consent"] is not True:
        raise BodyAccelerationError("explicit synthetic-data consent is required")
    stream_id = _native_text(data["stream_id"], "stream_id", MAX_STREAM_ID_LENGTH)

    names = data["joint_names"]
    if type(names) is not list or not 1 <= len(names) <= MAX_JOINTS:
        raise BodyAccelerationError("joint_names must be a bounded native list")
    clean_names = tuple(_native_text(name, "joint name", MAX_NAME_LENGTH) for name in names)
    if len(set(clean_names)) != len(clean_names):
        raise BodyAccelerationError("joint_names must be unique")

    limits_data = _native_dict(acceleration_limits, set(clean_names), "acceleration_limits")
    limits = tuple(
        _finite_number(limits_data[name], f"limit for {name}", MAX_ACCELERATION_LIMIT)
        for name in clean_names
    )
    if any(limit <= 0.0 for limit in limits):
        raise BodyAccelerationError("all acceleration limits must be positive")

    samples = data["samples"]
    if type(samples) is not list or not 3 <= len(samples) <= MAX_SAMPLES:
        raise BodyAccelerationError("samples must be a native list of 3..4096 frames")
    if len(samples) * len(clean_names) > MAX_POSITION_VALUES:
        raise BodyAccelerationError("trajectory exceeds the total position-value budget")

    timestamps: list[int] = []
    positions: list[tuple[float, ...]] = []
    for index, raw_sample in enumerate(samples):
        sample = _native_dict(raw_sample, {"time_ns", "positions"}, f"samples[{index}]")
        timestamp = sample["time_ns"]
        if type(timestamp) is not int or not 0 <= timestamp <= MAX_TIMESTAMP_NS:
            raise BodyAccelerationError(f"samples[{index}].time_ns must be a bounded native integer")
        if timestamps and timestamp <= timestamps[-1]:
            raise BodyAccelerationError("sample timestamps must be strictly increasing")
        timestamps.append(timestamp)
        raw_positions = sample["positions"]
        if type(raw_positions) is not list or len(raw_positions) != len(clean_names):
            raise BodyAccelerationError(f"samples[{index}].positions dimension mismatch")
        positions.append(tuple(
            _finite_number(value, f"samples[{index}].positions[{joint_index}]", MAX_ABS_POSITION)
            for joint_index, value in enumerate(raw_positions)
        ))

    peaks = [0.0] * len(clean_names)
    violations: list[AccelerationViolation] = []
    for middle in range(1, len(samples) - 1):
        dt_left = (timestamps[middle] - timestamps[middle - 1]) / 1_000_000_000.0
        dt_right = (timestamps[middle + 1] - timestamps[middle]) / 1_000_000_000.0
        span = dt_left + dt_right
        for joint_index, joint in enumerate(clean_names):
            before = positions[middle - 1][joint_index]
            current = positions[middle][joint_index]
            after = positions[middle + 1][joint_index]
            velocity_left = (current - before) / dt_left
            velocity_right = (after - current) / dt_right
            acceleration = 2.0 * (velocity_right - velocity_left) / span
            if not math.isfinite(acceleration):
                raise BodyAccelerationError("computed acceleration is not finite")
            magnitude = abs(acceleration)
            peaks[joint_index] = max(peaks[joint_index], magnitude)
            if magnitude > limits[joint_index]:
                violations.append(AccelerationViolation(joint, middle, acceleration, limits[joint_index]))

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
            [item.joint, item.middle_sample_index, item.acceleration, item.limit]
            for item in violations
        ],
    }
    canonical = json.dumps(
        digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    digest = hashlib.sha256(b"mojum.body.acceleration-audit/1" + bytes((0,)) + canonical).hexdigest()

    return BodyAccelerationReport(
        stream_id=stream_id,
        sample_count=len(samples),
        joint_count=len(clean_names),
        interval_count=len(samples) - 2,
        peak_abs_acceleration=tuple((name, peaks[index]) for index, name in enumerate(clean_names)),
        violations=tuple(violations),
        digest=digest,
    )
