"""Strict, detached metadata validation for synthetic rig DOF limits.

This module validates a caller-supplied declaration. It does not load a rig,
run kinematics/physics, or authorize motor commands.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


SCHEMA = "mojum.rig-dof-limits/1"
MAX_DOFS = 128
MAX_RIG_ID_LENGTH = 128
MAX_DO_ID_LENGTH = 64
AXIS_TOLERANCE = 1e-6
_MAX_REVISION = (1 << 63) - 1


class DOFLimitsError(ValueError):
    """Raised when an untrusted DOF-limit declaration is invalid."""


@dataclass(frozen=True)
class DOFLimit:
    dof_id: str
    kind: str
    unit: str
    axis: tuple[float, float, float]
    lower: float
    upper: float


@dataclass(frozen=True)
class RigDOFLimits:
    rig_id: str
    rig_revision: int
    dofs: tuple[DOFLimit, ...]
    digest: str

    def to_dict(self) -> dict:
        """Return a detached JSON-compatible representation."""
        return {
            "schema": SCHEMA,
            "rig_id": self.rig_id,
            "rig_revision": self.rig_revision,
            "dofs": [
                {
                    "id": item.dof_id,
                    "kind": item.kind,
                    "unit": item.unit,
                    "axis": list(item.axis),
                    "lower": item.lower,
                    "upper": item.upper,
                }
                for item in self.dofs
            ],
            "digest": self.digest,
        }


def _exact_keys(value: dict, expected: set[str], where: str) -> None:
    # Reject cardinality before materializing or inspecting caller-controlled keys.
    if len(value) != len(expected):
        raise DOFLimitsError(
            f"{where} keys mismatch (expected {len(expected)}, got {len(value)})"
        )
    keys = tuple(value.keys())
    if any(type(key) is not str for key in keys):
        raise DOFLimitsError(f"{where} keys must be exact strings")
    actual = set(keys)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise DOFLimitsError(f"{where} keys mismatch (missing={missing}, extra={extra})")


def _identifier(value: object, *, where: str, max_length: int) -> str:
    if type(value) is not str or not value or len(value) > max_length:
        raise DOFLimitsError(f"{where} must be a non-empty bounded string")
    if not value[0].isascii() or not value[0].isalnum():
        raise DOFLimitsError(f"{where} must start with an ASCII letter or digit")
    if any(not (ch.isascii() and (ch.isalnum() or ch in "._-")) for ch in value):
        raise DOFLimitsError(f"{where} contains unsupported characters")
    return value


def _finite_number(value: object, *, where: str) -> float:
    if type(value) not in (int, float):
        raise DOFLimitsError(f"{where} must be an int or float, not bool or subclass")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise DOFLimitsError(f"{where} is outside the finite numeric range") from None
    if not math.isfinite(result):
        raise DOFLimitsError(f"{where} must be finite")
    return result


def validate_dof_limits(payload: object) -> RigDOFLimits:
    """Validate a versioned PUBLIC_SYNTHETIC rig DOF declaration.

    Expected outer keys: schema, rig_id, rig_revision, dofs. Each DOF has
    exactly id, kind, unit, axis, lower, upper. Revolute axes use radians;
    prismatic axes use metres. Axes must already be normalized (no silent
    normalization or unit conversion is performed).
    """
    if type(payload) is not dict:
        raise DOFLimitsError("payload must be an exact dict")
    _exact_keys(payload, {"schema", "rig_id", "rig_revision", "dofs"}, "payload")
    if type(payload["schema"]) is not str or payload["schema"] != SCHEMA:
        raise DOFLimitsError("unsupported DOF-limit schema")
    rig_id = _identifier(payload["rig_id"], where="rig_id", max_length=MAX_RIG_ID_LENGTH)
    revision = payload["rig_revision"]
    if type(revision) is not int or not 0 <= revision <= _MAX_REVISION:
        raise DOFLimitsError("rig_revision must be a non-negative 63-bit exact int")
    raw_dofs = payload["dofs"]
    if type(raw_dofs) is not list or not 1 <= len(raw_dofs) <= MAX_DOFS:
        raise DOFLimitsError(f"dofs must be a list containing 1..{MAX_DOFS} entries")

    parsed: list[DOFLimit] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_dofs):
        where = f"dofs[{index}]"
        if type(raw) is not dict:
            raise DOFLimitsError(f"{where} must be an exact dict")
        _exact_keys(raw, {"id", "kind", "unit", "axis", "lower", "upper"}, where)
        dof_id = _identifier(raw["id"], where=f"{where}.id", max_length=MAX_DO_ID_LENGTH)
        if dof_id in seen:
            raise DOFLimitsError(f"duplicate DOF id: {dof_id}")
        seen.add(dof_id)

        kind = raw["kind"]
        unit = raw["unit"]
        if type(kind) is not str or type(unit) is not str:
            raise DOFLimitsError(f"{where} kind and unit must be exact strings")
        expected_unit = {"revolute": "rad", "prismatic": "m"}.get(kind)
        if expected_unit is None:
            raise DOFLimitsError(f"{where} has unsupported kind (only revolute/prismatic)")
        if unit != expected_unit:
            raise DOFLimitsError(f"{where} unit must be {expected_unit!r} for {kind}")

        axis_raw = raw["axis"]
        if type(axis_raw) is not list or len(axis_raw) != 3:
            raise DOFLimitsError(f"{where}.axis must contain exactly three numbers")
        axis = tuple(
            _finite_number(component, where=f"{where}.axis[{axis_index}]")
            for axis_index, component in enumerate(axis_raw)
        )
        norm = math.sqrt(sum(component * component for component in axis))
        if not math.isfinite(norm) or abs(norm - 1.0) > AXIS_TOLERANCE:
            raise DOFLimitsError(f"{where}.axis must be normalized within {AXIS_TOLERANCE:g}")

        lower = _finite_number(raw["lower"], where=f"{where}.lower")
        upper = _finite_number(raw["upper"], where=f"{where}.upper")
        if not lower < upper:
            raise DOFLimitsError(f"{where} requires lower < upper")
        parsed.append(DOFLimit(dof_id, kind, unit, axis, lower, upper))

    canonical = {
        "schema": SCHEMA,
        "rig_id": rig_id,
        "rig_revision": revision,
        "dofs": [
            {
                "id": item.dof_id,
                "kind": item.kind,
                "unit": item.unit,
                "axis": list(item.axis),
                "lower": item.lower,
                "upper": item.upper,
            }
            for item in parsed
        ],
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return RigDOFLimits(rig_id, revision, tuple(parsed), digest)
