"""Bounded offline continuity audit for PUBLIC_SYNTHETIC joint trajectories."""
from __future__ import annotations
import hashlib
import json
import math

SCHEMA = "mojum.joint-continuity/1"
TWO_PI = 2.0 * math.pi
MAX_SAMPLES, MAX_JOINTS, MAX_ID_LENGTH = 10_000, 512, 128
# Joint positions are radians. This generous finite domain prevents arithmetic
# overflow while allowing many revolutions for explicitly wrapped joints.
MAX_ABS_POSITION_RAD = 1.0e12


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _identifier(value):
    return (
        type(value) is str
        and 0 < len(value) <= MAX_ID_LENGTH
        and value.strip() == value
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _delta(previous, current, wrap):
    difference = current - previous
    if not math.isfinite(difference):
        raise ValueError("joint displacement overflow")
    if wrap:
        difference = (difference + math.pi) % TWO_PI - math.pi
    result = abs(difference)
    if not math.isfinite(result):
        raise ValueError("non-finite joint displacement")
    return result


def audit_joint_continuity(
    samples,
    limits,
    *,
    revolute=(),
    expected_revision=None,
    max_samples=MAX_SAMPLES,
    max_joints=MAX_JOINTS,
):
    """Audit consecutive position samples; caller declares radians and wrap semantics."""
    if type(max_samples) is not int or not 1 <= max_samples <= MAX_SAMPLES:
        raise ValueError("invalid max_samples")
    if type(max_joints) is not int or not 1 <= max_joints <= MAX_JOINTS:
        raise ValueError("invalid max_joints")
    if type(samples) not in (list, tuple) or not 1 <= len(samples) <= max_samples:
        raise ValueError("invalid samples")
    if type(limits) is not dict or not limits or len(limits) > max_joints:
        raise ValueError("invalid limits")
    if type(revolute) not in (list, tuple, set, frozenset):
        raise ValueError("invalid revolute set")
    if any(not _identifier(key) for key in limits):
        raise ValueError("invalid joint id")
    if any(not _number(value) or value <= 0 or value > TWO_PI for value in limits.values()):
        raise ValueError("invalid angular limit")
    if any(not _identifier(key) for key in revolute) or len(set(revolute)) != len(revolute):
        raise ValueError("invalid revolute id")
    if not set(revolute).issubset(limits):
        raise ValueError("unknown revolute joint")
    if expected_revision is not None and not _identifier(expected_revision):
        raise ValueError("invalid expected revision")

    joints = tuple(sorted(limits))
    previous = None
    previous_tick = None
    revision = None
    maxima = {joint: 0.0 for joint in joints}
    first_bad = {}
    normalized = []

    for index, row in enumerate(samples):
        if type(row) is not dict or set(row) != {"tick", "revision", "positions"}:
            raise ValueError("invalid sample shape")
        tick, row_revision, positions = row["tick"], row["revision"], row["positions"]
        if type(tick) is not int or tick < 0:
            raise ValueError("invalid tick")
        if not _identifier(row_revision):
            raise ValueError("invalid rig revision")
        if type(positions) is not dict or set(positions) != set(joints):
            raise ValueError("inconsistent joint set")
        if any(
            not _number(value) or abs(value) > MAX_ABS_POSITION_RAD
            for value in positions.values()
        ):
            raise ValueError("joint position outside finite angular domain")

        if index == 0:
            revision = row_revision
            if expected_revision is not None and row_revision != expected_revision:
                raise ValueError("unexpected rig revision")
        else:
            if tick != previous_tick + 1:
                raise ValueError("ticks must be consecutive")
            if row_revision != revision:
                raise ValueError("rig revision changed")
            for joint in joints:
                displacement = _delta(previous[joint], positions[joint], joint in revolute)
                maxima[joint] = max(maxima[joint], displacement)
                if displacement > limits[joint] and joint not in first_bad:
                    first_bad[joint] = tick

        normalized.append(
            {
                "tick": tick,
                "revision": row_revision,
                "positions": {joint: positions[joint] for joint in joints},
            }
        )
        previous, previous_tick = positions, tick

    violations = [
        {
            "joint": joint,
            "first_offending_tick": first_bad[joint],
            "max_step_rad": maxima[joint],
            "limit_rad": limits[joint],
        }
        for joint in joints
        if joint in first_bad
    ]
    payload = {
        "schema": SCHEMA,
        "revision": revision,
        "limits": {joint: limits[joint] for joint in joints},
        "revolute": sorted(revolute),
        "samples": normalized,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {
        "schema": SCHEMA,
        "ok": not violations,
        "revision": revision,
        "sample_count": len(normalized),
        "joint_count": len(joints),
        "max_steps_rad": maxima,
        "violations": violations,
        "digest_sha256": hashlib.sha256(raw).hexdigest(),
        "authority": "PUBLIC_SYNTHETIC_QA_ONLY",
    }
