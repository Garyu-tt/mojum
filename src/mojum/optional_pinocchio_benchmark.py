"""Opt-in virtual 2-DOF kinematics cross-check; no organism or actuator I/O.

The default path uses only stdlib. Pinocchio is imported ONLY for --backend pinocchio.
The independent analytic IK is verified against the selected backend's FK.
This is a research benchmark, not a physical contact/actuation simulator.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib import import_module, metadata
import json
import math
import random
import sys
from time import perf_counter_ns

SCHEMA = "mojum.kinematics.pinocchio-benchmark/1"
PIN_DISTRIBUTION_VERSION = "4.1.0"
MAX_CASES = 128
TOLERANCE_M = 1e-8


def _scalar(value: object, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError("expected a real scalar, not bool or other type")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError("unrepresentable scalar") from None
    if not math.isfinite(result) or abs(result) > 1000 or (positive and result <= 0):
        raise ValueError("scalar outside safe finite range")
    return result


@dataclass(frozen=True)
class PlanarRig:
    """Self-contained virtual fixture in metres and radians, not a real robot."""

    link1_m: float = 0.40
    link2_m: float = 0.30
    joint_limits_rad: tuple[tuple[float, float], tuple[float, float]] = (
        (-2.60, 2.60), (-2.50, 2.50)
    )

    def __post_init__(self) -> None:
        for value in (self.link1_m, self.link2_m):
            _scalar(value, positive=True)
            if value > 10:
                raise ValueError("unbounded synthetic link")
        limits = self.joint_limits_rad
        if type(limits) is not tuple or len(limits) != 2:
            raise ValueError("expected exactly two joint limits")
        for interval in limits:
            if type(interval) is not tuple or len(interval) != 2:
                raise ValueError("invalid joint limits")
            lo, hi = map(_scalar, interval)
            if not (-math.pi <= lo < hi <= math.pi):
                raise ValueError("joint limits outside [-pi, pi]")

    def validate_angles(self, q: tuple[float, float]) -> tuple[float, float]:
        if type(q) not in (tuple, list) or len(q) != 2:
            raise ValueError("expected exactly two angles")
        a, b = (_scalar(v) for v in q)
        if not (self.joint_limits_rad[0][0] <= a <= self.joint_limits_rad[0][1]
                and self.joint_limits_rad[1][0] <= b <= self.joint_limits_rad[1][1]):
            raise ValueError("joint limit violation")
        return (a, b)


def analytic_fk(rig: PlanarRig, q: tuple[float, float]) -> tuple[float, float]:
    a, b = rig.validate_angles(q)
    return (rig.link1_m * math.cos(a) + rig.link2_m * math.cos(a + b),
            rig.link1_m * math.sin(a) + rig.link2_m * math.sin(a + b))


def analytic_ik(rig: PlanarRig, target: tuple[float, float]) -> tuple[float, float]:
    """Closed-form two-link IK, elbow-positive first, or explicit rejection."""
    if type(target) not in (tuple, list) or len(target) != 2:
        raise ValueError("expected target (x, y)")
    x, y = (_scalar(v) for v in target)
    a, b = rig.link1_m, rig.link2_m
    cosine = (x * x + y * y - a * a - b * b) / (2 * a * b)
    if cosine < -1 - 1e-12 or cosine > 1 + 1e-12:
        raise ValueError("target unreachable")
    cosine = max(-1.0, min(1.0, cosine))
    sine = math.sqrt(max(0.0, 1 - cosine * cosine))
    for direction in (1.0, -1.0):
        q2 = math.atan2(direction * sine, cosine)
        q1 = math.atan2(y, x) - math.atan2(b * math.sin(q2), a + b * math.cos(q2))
        q1 = math.atan2(math.sin(q1), math.cos(q1))
        try:
            q = rig.validate_angles((q1, q2))
        except ValueError:
            continue
        if math.dist(analytic_fk(rig, q), (x, y)) <= TOLERANCE_M:
            return q
    raise ValueError("target has no solution within joint limits")


def fixture_urdf(rig: PlanarRig) -> str:
    """Only locally generated XML; never parses user paths, URDF or meshes."""
    lo1, hi1 = rig.joint_limits_rad[0]
    lo2, hi2 = rig.joint_limits_rad[1]
    return f'''<?xml version="1.0"?>
<robot name="mojum_virtual_planar_2dof">
<link name="base"/><link name="arm1"/><link name="arm2"/><link name="tool_tip"/>
<joint name="shoulder" type="revolute">
  <parent link="base"/><child link="arm1"/><origin xyz="0 0 0" rpy="0 0 0"/>
  <axis xyz="0 0 1"/><limit lower="{lo1}" upper="{hi1}" effort="1" velocity="1"/>
</joint>
<joint name="elbow" type="revolute">
  <parent link="arm1"/><child link="arm2"/><origin xyz="{rig.link1_m} 0 0" rpy="0 0 0"/>
  <axis xyz="0 0 1"/><limit lower="{lo2}" upper="{hi2}" effort="1" velocity="1"/>
</joint>
<joint name="tool_fixed" type="fixed">
  <parent link="arm2"/><child link="tool_tip"/>
  <origin xyz="{rig.link2_m} 0 0" rpy="0 0 0"/>
</joint>
</robot>'''


class PinocchioFK:
    """Explicitly version-gated optional foreign backend, read-only and virtual."""

    def __init__(self, rig: PlanarRig) -> None:
        try:
            installed = metadata.version("pin")
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError("optional dependency missing: install pin==4.1.0 in isolated venv") from exc
        if installed != PIN_DISTRIBUTION_VERSION:
            raise RuntimeError(f"unreviewed pin version {installed!r}; expected {PIN_DISTRIBUTION_VERSION}")
        pin = import_module("pinocchio")
        np = import_module("numpy")
        model = pin.buildModelFromXML(fixture_urdf(rig))
        if (model.nq, model.nv) != (2, 2):
            raise RuntimeError("unexpected Pinocchio joint degrees of freedom")
        tip = model.getFrameId("tool_tip")
        if tip >= len(model.frames):
            raise RuntimeError("Pinocchio tip frame missing")
        self.rig, self.pin, self.np, self.model, self.tip = rig, pin, np, model, tip
        self.data = model.createData()

    def __call__(self, q: tuple[float, float]) -> tuple[float, float]:
        angles = self.rig.validate_angles(q)
        self.pin.forwardKinematics(self.model, self.data, self.np.asarray(angles, dtype=float))
        self.pin.updateFramePlacements(self.model, self.data)
        position = self.data.oMf[self.tip].translation
        x, y, z = (float(position[i]) for i in range(3))
        if not all(math.isfinite(v) for v in (x, y, z)) or abs(z) > TOLERANCE_M:
            raise RuntimeError("invalid off-plane Pinocchio tip position")
        return (x, y)


def run_benchmark(*, backend: str = "analytic", seed: int = 702,
                  cases: int = 32, _fk_override=None) -> dict:
    """Benchmark fixed-seed reachable poses, never touches persisted state."""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be a uint32")
    if type(cases) is not int or not 1 <= cases <= MAX_CASES:
        raise ValueError("cases must be in [1, 128]")
    if backend not in ("analytic", "pinocchio"):
        raise ValueError("unknown backend")
    rig = PlanarRig()
    fk = _fk_override if _fk_override is not None else (
        PinocchioFK(rig) if backend == "pinocchio" else lambda q: analytic_fk(rig, q)
    )
    randomizer = random.Random(seed)
    max_fk_error = max_ik_error = 0.0
    durations = []
    for _ in range(cases):
        q = tuple(randomizer.uniform(lo * 0.80, hi * 0.80)
                  for lo, hi in rig.joint_limits_rad)
        expected = analytic_fk(rig, q)
        start = perf_counter_ns()
        actual = fk(q)
        if len(actual) != 2 or not all(math.isfinite(v) for v in actual):
            raise RuntimeError("foreign FK produced invalid coordinate")
        fk_error = math.dist(expected, actual)
        if fk_error > TOLERANCE_M:
            raise RuntimeError(f"FK divergence {fk_error:.6g} m")
        solved = analytic_ik(rig, expected)
        ik_error = math.dist(fk(solved), expected)
        if ik_error > TOLERANCE_M:
            raise RuntimeError(f"IK verification divergence {ik_error:.6g} m")
        durations.append(perf_counter_ns() - start)
        max_fk_error = max(max_fk_error, fk_error)
        max_ik_error = max(max_ik_error, ik_error)
    return {
        "schema": SCHEMA, "virtual_only": True, "backend": backend,
        "seed": seed, "cases": cases, "tolerance_m": TOLERANCE_M,
        "max_fk_error_m": max_fk_error, "max_ik_error_m": max_ik_error,
        "avg_case_ns": sum(durations) // cases,
        "min_case_ns": min(durations), "max_case_ns": max(durations),
        "pin_distribution": PIN_DISTRIBUTION_VERSION if backend == "pinocchio" else None,
        "limitations": "kinematics-only; no contact, cloth, torque or real hardware",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("analytic", "pinocchio"), default="analytic")
    parser.add_argument("--seed", type=int, default=702)
    parser.add_argument("--cases", type=int, default=32)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_benchmark(backend=args.backend, seed=args.seed, cases=args.cases),
                         sort_keys=True, separators=(",", ":")))
    except (ValueError, RuntimeError, ImportError) as exc:
        print(f"kinematics benchmark failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
