"""Offline admission for public synthetic LeRobot-style joint trajectories."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

SCHEMA = "mojum.lerobot-trajectory/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
CONSENT = "explicit"
POSITION_UNIT = "rad"
MAX_JOINTS = 256
MAX_FRAMES = 10_000
MAX_SAMPLES = 256_000
MAX_NAME_LENGTH = 96
MAX_TIMESTAMP_NS = (1 << 63) - 1
MAX_ABS_POSITION = 1_000_000.0


class TrajectoryError(ValueError):
    """Invalid trajectory admission input."""


@dataclass(frozen=True, slots=True)
class AdmittedTrajectory:
    schema: str
    joint_names: tuple[str, ...]
    timestamps_ns: tuple[int, ...]
    positions_rad: tuple[tuple[float, ...], ...]
    frame_count: int
    joint_count: int
    duration_ns: int
    sha256: str


def _dict(value: Any, field: str) -> dict:
    if type(value) is not dict:
        raise TrajectoryError(f"{field} must be a plain dict")
    return value


def _list(value: Any, field: str) -> list:
    if type(value) is not list:
        raise TrajectoryError(f"{field} must be a plain list")
    return value


def _name(value: Any, field: str) -> str:
    if (type(value) is not str or not value or len(value) > MAX_NAME_LENGTH
            or value.strip() != value or any(ord(ch) < 0x20 for ch in value)):
        raise TrajectoryError(f"{field} must be a bounded non-empty plain string")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TrajectoryError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _position(value: Any, field: str) -> float:
    if type(value) not in (int, float):
        raise TrajectoryError(f"{field} must be a native int or float")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise TrajectoryError(f"{field} is outside the supported numeric range") from None
    if not math.isfinite(number) or abs(number) > MAX_ABS_POSITION:
        raise TrajectoryError(f"{field} must be finite and within the supported magnitude")
    return 0.0 if number == 0.0 else number


def validate_trajectory(payload: Any) -> AdmittedTrajectory:
    """Validate and detach one PUBLIC_SYNTHETIC in-memory trajectory."""
    root = _dict(payload, "payload")
    required = {"schema", "provenance", "consent", "position_unit", "joint_names", "frames"}
    if set(root) != required:
        raise TrajectoryError("payload must contain exactly the v1 required keys")
    for key, expected in (
        ("schema", SCHEMA), ("provenance", PROVENANCE),
        ("consent", CONSENT), ("position_unit", POSITION_UNIT),
    ):
        if type(root[key]) is not str or root[key] != expected:
            raise TrajectoryError(f"unsupported {key}")

    names_in = _list(root["joint_names"], "joint_names")
    if not 1 <= len(names_in) <= MAX_JOINTS:
        raise TrajectoryError(f"joint_names must contain 1..{MAX_JOINTS} entries")
    names = tuple(_name(name, "joint name") for name in names_in)
    if len(set(names)) != len(names):
        raise TrajectoryError("joint_names must be unique")

    frames_in = _list(root["frames"], "frames")
    if not 1 <= len(frames_in) <= MAX_FRAMES:
        raise TrajectoryError(f"frames must contain 1..{MAX_FRAMES} entries")
    if len(frames_in) * len(names) > MAX_SAMPLES:
        raise TrajectoryError(f"trajectory exceeds {MAX_SAMPLES} scalar samples")

    timestamps: list[int] = []
    positions: list[tuple[float, ...]] = []
    previous = -1
    for index, raw_frame in enumerate(frames_in):
        frame = _dict(raw_frame, f"frames[{index}]")
        if set(frame) != {"timestamp_ns", "positions_rad"}:
            raise TrajectoryError(f"frames[{index}] has unknown or missing keys")
        timestamp = _integer(
            frame["timestamp_ns"], f"frames[{index}].timestamp_ns",
            0, MAX_TIMESTAMP_NS,
        )
        if timestamp <= previous:
            raise TrajectoryError("timestamps must be strictly increasing")
        previous = timestamp
        vector = _list(frame["positions_rad"], f"frames[{index}].positions_rad")
        if len(vector) != len(names):
            raise TrajectoryError(f"frames[{index}] position dimension mismatch")
        positions.append(tuple(
            _position(value, f"frames[{index}].positions_rad[{column}]")
            for column, value in enumerate(vector)
        ))
        timestamps.append(timestamp)

    canonical = {
        "schema": SCHEMA, "provenance": PROVENANCE, "consent": CONSENT,
        "position_unit": POSITION_UNIT, "joint_names": list(names),
        "frames": [
            {"timestamp_ns": stamp, "positions_rad": list(values)}
            for stamp, values in zip(timestamps, positions)
        ],
    }
    encoded = json.dumps(
        canonical, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return AdmittedTrajectory(
        schema=SCHEMA, joint_names=names, timestamps_ns=tuple(timestamps),
        positions_rad=tuple(positions), frame_count=len(timestamps),
        joint_count=len(names), duration_ns=timestamps[-1] - timestamps[0],
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
