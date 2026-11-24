"""Dependency-free planar two-link FK/IK reference for benchmark fixtures."""
from __future__ import annotations

from dataclasses import dataclass
import math

# Keep arithmetic bounded so squaring and products in the analytic solver
# cannot overflow even when callers provide finite but absurd magnitudes.
_MAX_ABS = 1e100
_MIN_LINK_LENGTH = 1e-150


def _finite_number(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a native int or float")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{field} is outside finite numeric bounds") from None
    if not math.isfinite(result) or abs(result) > _MAX_ABS:
        raise ValueError(f"{field} must be finite and within numeric bounds")
    return result


@dataclass(frozen=True)
class JointLimits:
    shoulder_min: float = -math.pi
    shoulder_max: float = math.pi
    elbow_min: float = -math.pi
    elbow_max: float = math.pi

    def __post_init__(self):
        values = tuple(_finite_number(v, "joint limit") for v in (
            self.shoulder_min, self.shoulder_max, self.elbow_min, self.elbow_max
        ))
        if values[0] > values[1] or values[2] > values[3]:
            raise ValueError("joint minimum must not exceed maximum")


@dataclass(frozen=True)
class JointState:
    shoulder: float
    elbow: float

    def __post_init__(self):
        _finite_number(self.shoulder, "shoulder angle")
        _finite_number(self.elbow, "elbow angle")


@dataclass(frozen=True)
class Point2:
    x: float
    y: float

    def __post_init__(self):
        _finite_number(self.x, "point x")
        _finite_number(self.y, "point y")


@dataclass(frozen=True)
class IKResult:
    joints: JointState
    position: Point2
    residual_m: float
    branch: str


def _validate_lengths(link1_m: float, link2_m: float) -> None:
    first = _finite_number(link1_m, "link1_m")
    second = _finite_number(link2_m, "link2_m")
    if first < _MIN_LINK_LENGTH or second < _MIN_LINK_LENGTH:
        raise ValueError("link lengths must be at least 1e-150 metres")


def within_limits(joints: JointState, limits: JointLimits, *, tolerance: float = 1e-12) -> bool:
    if type(joints) is not JointState or type(limits) is not JointLimits:
        raise ValueError("joints and limits must use the reference dataclasses")
    tol = _finite_number(tolerance, "tolerance")
    if tol < 0:
        raise ValueError("tolerance must be finite and non-negative")
    return (limits.shoulder_min - tol <= joints.shoulder <= limits.shoulder_max + tol
            and limits.elbow_min - tol <= joints.elbow <= limits.elbow_max + tol)


def forward_kinematics(link1_m: float, link2_m: float, joints: JointState) -> Point2:
    _validate_lengths(link1_m, link2_m)
    if type(joints) is not JointState:
        raise ValueError("joints must be a JointState")
    angle2 = joints.shoulder + joints.elbow
    return Point2(link1_m * math.cos(joints.shoulder) + link2_m * math.cos(angle2),
                  link1_m * math.sin(joints.shoulder) + link2_m * math.sin(angle2))


def inverse_kinematics(link1_m: float, link2_m: float, target: Point2, *,
                       branch: str = "elbow_up", limits: JointLimits | None = None,
                       tolerance_m: float = 1e-9) -> IKResult | None:
    """Analytic planar IK. Returns None for unreachable or limit-violating targets."""
    _validate_lengths(link1_m, link2_m)
    if type(target) is not Point2:
        raise ValueError("target must be a Point2")
    if limits is not None and type(limits) is not JointLimits:
        raise ValueError("limits must be a JointLimits")
    if type(branch) is not str or branch not in ("elbow_up", "elbow_down"):
        raise ValueError("branch must be 'elbow_up' or 'elbow_down'")
    tolerance = _finite_number(tolerance_m, "tolerance_m")
    if tolerance < 0:
        raise ValueError("tolerance_m must be finite and non-negative")
    r2 = target.x * target.x + target.y * target.y
    c2 = (r2 - link1_m * link1_m - link2_m * link2_m) / (2 * link1_m * link2_m)
    if c2 < -1.0 - 1e-12 or c2 > 1.0 + 1e-12:
        return None
    magnitude = math.acos(min(1.0, max(-1.0, c2)))
    elbow = magnitude if branch == "elbow_up" else -magnitude
    shoulder = math.atan2(target.y, target.x) - math.atan2(
        link2_m * math.sin(elbow), link1_m + link2_m * math.cos(elbow))
    joints = JointState(shoulder, elbow)
    if limits is not None and not within_limits(joints, limits):
        return None
    achieved = forward_kinematics(link1_m, link2_m, joints)
    residual = math.hypot(achieved.x - target.x, achieved.y - target.y)
    if residual > tolerance:
        return None
    return IKResult(joints, achieved, residual, branch)


def solve_both_branches(link1_m: float, link2_m: float, target: Point2, *,
                        limits: JointLimits | None = None) -> tuple[IKResult, ...]:
    results = []
    for branch in ("elbow_up", "elbow_down"):
        result = inverse_kinematics(link1_m, link2_m, target, branch=branch, limits=limits)
        if result is not None:
            results.append(result)
    return tuple(results)
