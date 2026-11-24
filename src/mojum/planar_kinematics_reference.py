"""Dependency-free planar two-link FK/IK reference for benchmark fixtures."""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class JointLimits:
    shoulder_min: float = -math.pi
    shoulder_max: float = math.pi
    elbow_min: float = -math.pi
    elbow_max: float = math.pi
    def __post_init__(self):
        values = (self.shoulder_min, self.shoulder_max, self.elbow_min, self.elbow_max)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("joint limits must be finite")
        if self.shoulder_min > self.shoulder_max or self.elbow_min > self.elbow_max:
            raise ValueError("joint minimum must not exceed maximum")

@dataclass(frozen=True)
class JointState:
    shoulder: float
    elbow: float
    def __post_init__(self):
        if not math.isfinite(self.shoulder) or not math.isfinite(self.elbow):
            raise ValueError("joint angles must be finite")

@dataclass(frozen=True)
class Point2:
    x: float
    y: float
    def __post_init__(self):
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("point coordinates must be finite")

@dataclass(frozen=True)
class IKResult:
    joints: JointState
    position: Point2
    residual_m: float
    branch: str

def _validate_lengths(link1_m: float, link2_m: float) -> None:
    if not math.isfinite(link1_m) or not math.isfinite(link2_m):
        raise ValueError("link lengths must be finite")
    if link1_m <= 0 or link2_m <= 0:
        raise ValueError("link lengths must be positive")

def within_limits(joints: JointState, limits: JointLimits, *, tolerance: float = 1e-12) -> bool:
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and non-negative")
    return (limits.shoulder_min - tolerance <= joints.shoulder <= limits.shoulder_max + tolerance
            and limits.elbow_min - tolerance <= joints.elbow <= limits.elbow_max + tolerance)

def forward_kinematics(link1_m: float, link2_m: float, joints: JointState) -> Point2:
    _validate_lengths(link1_m, link2_m)
    angle2 = joints.shoulder + joints.elbow
    return Point2(link1_m * math.cos(joints.shoulder) + link2_m * math.cos(angle2),
                  link1_m * math.sin(joints.shoulder) + link2_m * math.sin(angle2))

def inverse_kinematics(link1_m: float, link2_m: float, target: Point2, *,
                       branch: str = "elbow_up", limits: JointLimits | None = None,
                       tolerance_m: float = 1e-9) -> IKResult | None:
    """Analytic planar IK. Returns None for unreachable or limit-violating targets."""
    _validate_lengths(link1_m, link2_m)
    if branch not in ("elbow_up", "elbow_down"):
        raise ValueError("branch must be 'elbow_up' or 'elbow_down'")
    if not math.isfinite(tolerance_m) or tolerance_m < 0:
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
    if residual > tolerance_m:
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
