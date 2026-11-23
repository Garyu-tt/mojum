"""Bounded, read-only forward kinematics for synthetic articulated rigs.

This module evaluates caller-supplied joint transforms. It is not a physics
engine, pose controller, calibrated body model, or actuator interface.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

_INPUT_SCHEMA = "mojum.virtual-rig/1"
_OUTPUT_SCHEMA = "mojum.virtual-kinematics-result/1"
_VISIBILITY = "PUBLIC_SYNTHETIC"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_MAX_JOINTS = 128
_MAX_ABS_METRES = 1_000_000.0
_MAX_ABS_ANGLE = 1_000_000.0
_I64_MAX = 2**63 - 1
_INPUT_KEYS = frozenset({"schema", "visibility", "root", "joints"})
_JOINT_KEYS = frozenset({
    "id", "parent", "origin_m", "axis", "angle_rad", "min_rad", "max_rad",
})
_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _finite(value: object, maximum: float) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and abs(value) <= maximum
    except (TypeError, ValueError, OverflowError):
        return False


def _name(value: object) -> bool:
    return type(value) is str and _NAME.fullmatch(value) is not None


def _vec3(value: object, *, maximum: float) -> tuple[float, float, float]:
    if type(value) is not list or len(value) != 3:
        raise ValueError("expected a three-component vector")
    if not all(_finite(component, maximum) for component in value):
        raise ValueError("vector contains a nonfinite or out-of-range component")
    return tuple(float(component) for component in value)


def _mat_vec(matrix, vector):
    return tuple(
        matrix[row][0] * vector[0]
        + matrix[row][1] * vector[1]
        + matrix[row][2] * vector[2]
        for row in range(3)
    )


def _mat_mul(left, right):
    return tuple(
        tuple(
            sum(left[row][k] * right[k][column] for k in range(3))
            for column in range(3)
        )
        for row in range(3)
    )


def _axis_rotation(axis, angle):
    length = math.sqrt(sum(component * component for component in axis))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("joint axis must have nonzero length")
    x, y, z = (component / length for component in axis)
    cosine, sine = math.cos(angle), math.sin(angle)
    one_minus_cosine = 1.0 - cosine
    return (
        (
            cosine + x * x * one_minus_cosine,
            x * y * one_minus_cosine - z * sine,
            x * z * one_minus_cosine + y * sine,
        ),
        (
            y * x * one_minus_cosine + z * sine,
            cosine + y * y * one_minus_cosine,
            y * z * one_minus_cosine - x * sine,
        ),
        (
            z * x * one_minus_cosine - y * sine,
            z * y * one_minus_cosine + x * sine,
            cosine + z * z * one_minus_cosine,
        ),
    )


def _canonical_digest(payload: dict) -> str:
    try:
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ValueError("payload is not canonical JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def evaluate_forward_kinematics(rig: object) -> dict[str, Any]:
    """Evaluate a PUBLIC_SYNTHETIC parent-before-child revolute-joint rig.

    Each joint's origin is expressed in its parent's local coordinate frame.
    The local axis-angle rotation is applied after that translation. Output
    positions are metres and matrices are row-major 3x3 local-to-world
    rotations. The returned report is detached and digest-anchored.
    """
    if (type(rig) is not dict or any(type(key) is not str for key in rig)
            or set(rig) != _INPUT_KEYS):
        raise ValueError("invalid virtual rig keys")
    if type(rig["schema"]) is not str or rig["schema"] != _INPUT_SCHEMA:
        raise ValueError("unsupported virtual rig schema")
    if type(rig["visibility"]) is not str or rig["visibility"] != _VISIBILITY:
        raise ValueError("only PUBLIC_SYNTHETIC rigs are accepted")
    root = rig["root"]
    if not _name(root):
        raise ValueError("invalid root joint identifier")
    joints = rig["joints"]
    if type(joints) is not list or not 1 <= len(joints) <= _MAX_JOINTS:
        raise ValueError("joint count outside supported bounds")

    positions: dict[str, tuple[float, float, float]] = {}
    rotations: dict[str, tuple[tuple[float, float, float], ...]] = {}
    results = []
    root_count = 0

    for index, joint in enumerate(joints):
        if (type(joint) is not dict or any(type(key) is not str for key in joint)
                or set(joint) != _JOINT_KEYS):
            raise ValueError("invalid joint fields")
        joint_id, parent = joint["id"], joint["parent"]
        if not _name(joint_id) or joint_id in positions:
            raise ValueError("invalid or duplicate joint id")
        if parent is None:
            root_count += 1
            if index != 0 or joint_id != root or len(joints) < 1:
                raise ValueError("root must be the first and only root joint")
            parent_position, parent_rotation = (0.0, 0.0, 0.0), _IDENTITY
        else:
            if not _name(parent) or parent not in positions:
                raise ValueError("parent must refer to an earlier joint")
            parent_position = positions[parent]
            parent_rotation = rotations[parent]

        origin = _vec3(joint["origin_m"], maximum=_MAX_ABS_METRES)
        axis = _vec3(joint["axis"], maximum=1_000_000.0)
        angle, minimum, maximum = (
            joint["angle_rad"], joint["min_rad"], joint["max_rad"]
        )
        if not all(_finite(value, _MAX_ABS_ANGLE)
                   for value in (angle, minimum, maximum)):
            raise ValueError("joint angles and limits must be finite and bounded")
        if minimum > maximum or angle < minimum or angle > maximum:
            raise ValueError("joint angle is outside its declared limits")

        local_rotation = _axis_rotation(axis, float(angle))
        world_rotation = _mat_mul(parent_rotation, local_rotation)
        rotated_origin = _mat_vec(parent_rotation, origin)
        world_position = tuple(
            parent_position[i] + rotated_origin[i] for i in range(3)
        )
        if not all(math.isfinite(value) and abs(value) <= _MAX_ABS_METRES
                   for value in world_position):
            raise ValueError("world position exceeds supported bounds")
        if not all(math.isfinite(value) for row in world_rotation for value in row):
            raise ValueError("world rotation is nonfinite")

        positions[joint_id] = world_position
        rotations[joint_id] = world_rotation
        results.append({
            "id": joint_id,
            "parent": parent,
            "position_m": [round(value, 12) for value in world_position],
            "rotation_matrix": [
                [round(value, 12) for value in row] for row in world_rotation
            ],
        })

    if root_count != 1:
        raise ValueError("rig must contain exactly one root joint")

    report = {
        "schema": _OUTPUT_SCHEMA,
        "source_schema": _INPUT_SCHEMA,
        "visibility": _VISIBILITY,
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "joint_count": len(results),
        "root": root,
        "joints": results,
    }
    report["sha256"] = _canonical_digest(report)
    # Final serialization rejects any accidental non-JSON value and returns
    # data that shares no mutable lists/dicts with caller input or internals.
    return json.loads(json.dumps(
        report, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    ))


def _utf8_size_within(value: str, remaining: int) -> int | None:
    """Count UTF-8 bytes without allocating an encoded copy of caller text."""
    size = 0
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            return None
        if code <= 0x7F:
            size += 1
        elif code <= 0x7FF:
            size += 2
        elif code <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > remaining:
            return None
    return size


def _bounded_native_json_tree(value: object) -> bool:
    """Admit only a small acyclic tree of exact built-in JSON value types."""
    pending = [(value, 0)]
    seen: set[int] = set()
    visited = 0
    text_bytes = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > 8192 or depth > 16:
            return False
        kind = type(current)
        if kind is dict:
            identity = id(current)
            if identity in seen or len(current) > 4096:
                return False
            seen.add(identity)
            for key, child in current.items():
                if type(key) is not str:
                    return False
                size = _utf8_size_within(key, 262144 - text_bytes)
                if size is None:
                    return False
                text_bytes += size
                pending.append((child, depth + 1))
        elif kind is list:
            identity = id(current)
            if identity in seen or len(current) > 4096:
                return False
            seen.add(identity)
            pending.extend((child, depth + 1) for child in current)
        elif kind is str:
            size = _utf8_size_within(current, 262144 - text_bytes)
            if size is None:
                return False
            text_bytes += size
        elif kind is int:
            if current.bit_length() > 256:
                return False
        elif kind is float:
            if not math.isfinite(current):
                return False
        elif kind not in (bool, type(None)):
            return False
    return True


def verify_kinematics_report(report: object) -> bool:
    """Check a bounded native JSON report's self-consistency, not authenticity."""
    if type(report) is not dict or not _bounded_native_json_tree(report):
        return False
    digest = report.get("sha256")
    if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest, re.ASCII):
        return False
    unsigned = dict(report)
    supplied = unsigned.pop("sha256")
    try:
        return _canonical_digest(unsigned) == supplied
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        return False
