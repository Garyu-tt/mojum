"""Opt-in, public-fixture-only Pinocchio FK/IK spike (issue #702).

Never imports the optional robotics package during ordinary Mojum startup.
No journal, network, hardware, or real-organism data crosses this boundary.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import statistics
import time
from dataclasses import dataclass

SCHEMA = "mojum.benchmark.pinocchio-2r/1"
PIN_VERSION = "4.1.0"
LINK_LENGTHS_M = (0.35, 0.25)
JOINT_LIMITS_RAD = ((-2.8, 2.8), (-2.5, 2.5))
# Public, fixed joint targets. Not inferred from any persisted creature or pose.
TARGETS_RAD = ((0.25, 0.45), (-0.6, 0.8), (1.1, -0.5), (-1.2, 0.3))


def _angle(value: object, index: int) -> float:
    if type(value) not in (int, float):
        raise ValueError("joint angle must be a finite number, not bool")
    try:
        valid = math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid or not JOINT_LIMITS_RAD[index][0] <= value <= JOINT_LIMITS_RAD[index][1]:
        raise ValueError("joint angle exceeds hard safety bounds")
    return float(value)


def validate_configuration(q: object) -> tuple[float, float]:
    if type(q) not in (tuple, list) or len(q) != 2:
        raise ValueError("configuration must contain exactly two joint angles")
    return _angle(q[0], 0), _angle(q[1], 1)


def reference_fk(q: object) -> tuple[float, float]:
    """Independent planar 2R trigonometric control, not Pinocchio output."""
    shoulder, elbow = validate_configuration(q)
    a, b = LINK_LENGTHS_M
    return (a * math.cos(shoulder) + b * math.cos(shoulder + elbow),
            a * math.sin(shoulder) + b * math.sin(shoulder + elbow))


def _real_backend():
    try:
        np = importlib.import_module("numpy")
        pin = importlib.import_module("pinocchio")
    except ImportError as exc:
        raise RuntimeError("real Pinocchio benchmark requires optional pin==4.1.0") from exc
    version = str(getattr(pin, "__version__", ""))
    if version != PIN_VERSION:
        raise RuntimeError(f"unsupported Pinocchio version {version!r}; expected {PIN_VERSION}")
    return pin, np


@dataclass
class _Pinocchio2R:
    """Two revolute Z joints and wrist frame; synthetic and never actuated."""

    pin: object
    np: object
    model: object
    data: object
    frame_id: int

    @classmethod
    def create(cls):
        pin, np = _real_backend()
        model = pin.Model()
        shoulder = model.addJoint(0, pin.JointModelRZ(), pin.SE3.Identity(), "shoulder_z")
        elbow = model.addJoint(
            shoulder, pin.JointModelRZ(),
            pin.SE3(np.eye(3), np.array([LINK_LENGTHS_M[0], 0.0, 0.0])), "elbow_z")
        wrist = pin.Frame(
            "wrist_synthetic", elbow, 0,
            pin.SE3(np.eye(3), np.array([LINK_LENGTHS_M[1], 0.0, 0.0])),
            pin.FrameType.OP_FRAME)
        frame_id = model.addFrame(wrist)
        if model.nq != 2 or model.nv != 2:
            raise RuntimeError("unexpected Pinocchio model DOF")
        return cls(pin, np, model, model.createData(), frame_id)

    def fk(self, q: object) -> tuple[float, float]:
        angles = validate_configuration(q)
        self.pin.forwardKinematics(self.model, self.data, self.np.asarray(angles))
        self.pin.updateFramePlacements(self.model, self.data)
        point = self.data.oMf[self.frame_id].translation
        result = (float(point[0]), float(point[1]))
        if not all(math.isfinite(v) for v in result):
            raise RuntimeError("nonfinite result from Pinocchio")
        return result

    def ik(self, xy: tuple[float, float], start: object, *, max_steps: int = 96):
        if (type(max_steps) is not int or not 1 <= max_steps <= 256
                or type(xy) not in (tuple, list) or len(xy) != 2
                or any(type(v) not in (int, float) for v in xy)):
            raise ValueError("invalid IK target or iteration budget")
        # math.isfinite(10**1000) itself raises OverflowError: reject
        # unrepresentable targets before allocating any native/numpy state.
        try:
            target_finite = all(math.isfinite(v) for v in xy)
        except (OverflowError, ValueError, TypeError):
            target_finite = False
        if not target_finite:
            raise ValueError("invalid IK target or iteration budget")
        q = self.np.asarray(validate_configuration(start), dtype=float)
        target = self.np.asarray(xy, dtype=float)
        lower = self.np.asarray([x[0] for x in JOINT_LIMITS_RAD])
        upper = self.np.asarray([x[1] for x in JOINT_LIMITS_RAD])
        for iteration in range(max_steps):
            pos = self.np.asarray(self.fk((float(q[0]), float(q[1]))))
            error = target - pos
            residual = float(self.np.linalg.norm(error))
            if residual <= 1e-7:
                return tuple(float(v) for v in q), iteration, residual
            jac = self.pin.computeFrameJacobian(
                self.model, self.data, q, self.frame_id,
                self.pin.ReferenceFrame.LOCAL_WORLD_ALIGNED)
            planar = jac[:2, :]
            # Damped least squares: no unbounded jump on singular Jacobians.
            delta = self.np.linalg.solve(
                planar.T @ planar + 1e-4 * self.np.eye(2), planar.T @ error)
            q = self.np.clip(q + self.np.clip(delta, -0.18, 0.18), lower, upper)
        residual = math.dist(self.fk(tuple(float(v) for v in q)), xy)
        return tuple(float(v) for v in q), max_steps, residual


def run_benchmark(*, backend: str, samples: int = 128) -> dict:
    """Return a public-only report; timings are measurements, never invented."""
    if type(backend) is not str or backend not in ("reference", "pinocchio"):
        raise ValueError("unsupported backend")
    if type(samples) is not int or not 1 <= samples <= 4096:
        raise ValueError("samples must be integer in 1..4096")
    engine = _Pinocchio2R.create() if backend == "pinocchio" else None
    fk = engine.fk if engine else reference_fk
    cases = []
    durations = []
    for target_q in TARGETS_RAD:
        expected = reference_fk(target_q)
        actual = fk(target_q)
        discrepancy = math.dist(expected, actual)
        if discrepancy > 1e-9:
            raise RuntimeError("backend differs from independent analytic FK")
        case = {"target_joint_rad": list(target_q), "fk_error_m": discrepancy}
        if engine is not None:
            start = (target_q[0] + 0.08, target_q[1] - 0.08)
            solution, iterations, residual = engine.ik(expected, start)
            if residual > 1e-6:
                raise RuntimeError("Pinocchio IK did not converge within bounds")
            case.update({"ik_solution_rad": list(solution),
                         "ik_iterations": iterations, "ik_residual_m": residual})
        cases.append(case)
    for index in range(samples):
        q = TARGETS_RAD[index % len(TARGETS_RAD)]
        started = time.perf_counter_ns()
        fk(q)
        durations.append(time.perf_counter_ns() - started)
    return {"schema": SCHEMA,
            "backend": backend,
            "backend_version": PIN_VERSION if engine is not None else "stdlib-analytic/1",
            "real_pinocchio_executed": engine is not None,
            "fixture": "public-synthetic-2r", "physical_contact": False,
            "joint_limits_rad": [list(p) for p in JOINT_LIMITS_RAD],
            "link_lengths_m": list(LINK_LENGTHS_M),
            "samples": samples, "fk_median_ns": int(statistics.median(durations)),
            "cases": cases}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline synthetic 2R Pinocchio FK/IK spike")
    parser.add_argument("--backend", choices=("reference", "pinocchio"), default="reference")
    parser.add_argument("--samples", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(backend=args.backend, samples=args.samples)
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, f"benchmark unavailable: {exc}\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
