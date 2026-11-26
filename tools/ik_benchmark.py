#!/usr/bin/env python3
"""Dependency-free, deterministic planar IK benchmark fixtures.

This is a numerical benchmark, not a rigid-body/contact simulator and not a
Pinocchio or Jolt binding. All coordinates are synthetic and caller supplied.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time
import tracemalloc
from typing import Sequence

SCHEMA = "mojum.ik-benchmark/1"
MAX_JOINTS = 16
MAX_CASES = 256
MAX_ITERATIONS = 4096
MAX_ABS_COORDINATE = 1_000_000.0


def _finite_number(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be an exact int or float")
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_ABS_COORDINATE:
        raise ValueError(f"{name} must be finite and within the coordinate budget")
    return result


def _vector(values: Sequence[object], name: str, *, minimum: int, maximum: int) -> tuple[float, ...]:
    if type(values) not in (tuple, list) or not minimum <= len(values) <= maximum:
        raise ValueError(f"{name} must contain {minimum}..{maximum} values")
    return tuple(_finite_number(v, f"{name}[{i}]") for i, v in enumerate(values))


def _limits(limits: Sequence[Sequence[object]], count: int) -> tuple[tuple[float, float], ...]:
    if type(limits) not in (tuple, list) or len(limits) != count:
        raise ValueError("limits must have one pair per joint")
    result = []
    for i, pair in enumerate(limits):
        if type(pair) not in (tuple, list) or len(pair) != 2:
            raise ValueError(f"limits[{i}] must be a pair")
        low, high = (_finite_number(v, f"limits[{i}]") for v in pair)
        if low >= high or high - low > 2.0 * math.pi + 1e-12:
            raise ValueError(f"limits[{i}] must be ordered and span at most one revolution")
        result.append((low, high))
    return tuple(result)


@dataclass(frozen=True)
class IKResult:
    schema: str
    solver: str
    joint_angles_rad: tuple[float, ...]
    endpoint_xy: tuple[float, float]
    target_xy: tuple[float, float]
    residual_m: float
    iterations: int
    converged: bool
    evaluations: int
    digest_sha256: str


@dataclass(frozen=True)
class BenchmarkRow:
    case_id: str
    solver: str
    residual_m: float
    iterations: int
    evaluations: int
    elapsed_ns: int
    peak_bytes: int
    converged: bool
    digest_sha256: str


def _forward(lengths: tuple[float, ...], angles: tuple[float, ...]) -> tuple[float, float]:
    x = y = heading = 0.0
    for length, angle in zip(lengths, angles):
        heading += angle
        x += length * math.cos(heading)
        y += length * math.sin(heading)
    return x, y


def _result(solver: str, lengths: tuple[float, ...], angles: tuple[float, ...],
            target: tuple[float, float], iterations: int, evaluations: int,
            tolerance: float) -> IKResult:
    endpoint = _forward(lengths, angles)
    residual = math.hypot(target[0] - endpoint[0], target[1] - endpoint[1])
    payload = {
        "schema": SCHEMA, "solver": solver, "lengths_m": lengths,
        "angles_rad": angles, "endpoint_xy": endpoint, "target_xy": target,
        "residual_m": residual, "iterations": iterations,
        "evaluations": evaluations, "converged": residual <= tolerance,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode("utf-8")).hexdigest()
    return IKResult(SCHEMA, solver, angles, endpoint, target, residual,
                    iterations, residual <= tolerance, evaluations, digest)


def solve_ccd(lengths: Sequence[object], target_xy: Sequence[object],
              limits: Sequence[Sequence[object]], *, initial_angles: Sequence[object] | None = None,
              max_iterations: int = 256, tolerance: float = 1e-6) -> IKResult:
    """Bounded planar cyclic-coordinate-descent solver with absolute angle limits."""
    lens = _vector(lengths, "lengths", minimum=1, maximum=MAX_JOINTS)
    if any(v <= 0.0 for v in lens):
        raise ValueError("link lengths must be positive")
    target_values = _vector(target_xy, "target_xy", minimum=2, maximum=2)
    target = (target_values[0], target_values[1])
    bounds = _limits(limits, len(lens))
    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_ITERATIONS:
        raise ValueError("max_iterations outside budget")
    tol = _finite_number(tolerance, "tolerance")
    if not 0.0 < tol <= 1.0:
        raise ValueError("tolerance outside budget")
    if initial_angles is None:
        angles = [0.0] * len(lens)
    else:
        angles = list(_vector(initial_angles, "initial_angles", minimum=len(lens), maximum=len(lens)))
    for i, (low, high) in enumerate(bounds):
        if not low <= angles[i] <= high:
            raise ValueError("initial angle outside joint limits")
    evaluations = 0
    iterations = 0
    for iteration in range(max_iterations):
        iterations = iteration + 1
        for joint in range(len(lens) - 1, -1, -1):
            endpoint = _forward(lens, tuple(angles))
            evaluations += 1
            pivot = _forward(lens[:joint], tuple(angles[:joint])) if joint else (0.0, 0.0)
            evaluations += 1
            ax, ay = endpoint[0] - pivot[0], endpoint[1] - pivot[1]
            bx, by = target[0] - pivot[0], target[1] - pivot[1]
            if math.hypot(ax, ay) <= 1e-15 or math.hypot(bx, by) <= 1e-15:
                continue
            delta = math.atan2(ax * by - ay * bx, ax * bx + ay * by)
            low, high = bounds[joint]
            angles[joint] = min(high, max(low, angles[joint] + delta))
        current = _forward(lens, tuple(angles))
        evaluations += 1
        if math.hypot(target[0] - current[0], target[1] - current[1]) <= tol:
            break
    return _result("ccd", lens, tuple(angles), target, iterations, evaluations, tol)


def solve_two_link(lengths: Sequence[object], target_xy: Sequence[object],
                   limits: Sequence[Sequence[object]], *, elbow: str = "positive",
                   tolerance: float = 1e-6) -> IKResult:
    """Closed-form two-link baseline; rejects unreachable or limit-violating poses."""
    lens = _vector(lengths, "lengths", minimum=2, maximum=2)
    if any(v <= 0.0 for v in lens):
        raise ValueError("link lengths must be positive")
    target_values = _vector(target_xy, "target_xy", minimum=2, maximum=2)
    target = (target_values[0], target_values[1])
    bounds = _limits(limits, 2)
    tol = _finite_number(tolerance, "tolerance")
    if not 0.0 < tol <= 1.0:
        raise ValueError("tolerance outside budget")
    if elbow not in ("positive", "negative"):
        raise ValueError("elbow must be positive or negative")
    x, y = target
    distance2 = x * x + y * y
    l1, l2 = lens
    cosine = (distance2 - l1*l1 - l2*l2) / (2.0*l1*l2)
    if cosine < -1.0 - 1e-12 or cosine > 1.0 + 1e-12:
        raise ValueError("target is unreachable")
    cosine = min(1.0, max(-1.0, cosine))
    sign = 1.0 if elbow == "positive" else -1.0
    q2 = sign * math.acos(cosine)
    q1 = math.atan2(y, x) - math.atan2(l2 * math.sin(q2), l1 + l2 * math.cos(q2))
    angles = (q1, q2)
    if any(not low <= angle <= high for angle, (low, high) in zip(angles, bounds)):
        raise ValueError("analytic solution violates joint limits")
    return _result("two_link_analytic", lens, angles, target, 1, 1, tol)


def run_benchmark(cases: Sequence[dict[str, object]], *, max_iterations: int = 256,
                  tolerance: float = 1e-6) -> tuple[BenchmarkRow, ...]:
    """Run bounded synthetic cases; timings and peak bytes are observational only."""
    if type(cases) not in (tuple, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("cases must contain 1..256 entries")
    rows = []
    seen = set()
    for index, case in enumerate(cases):
        if type(case) is not dict:
            raise ValueError(f"case {index} must be a dict")
        case_id = case.get("case_id")
        if type(case_id) is not str or not case_id or len(case_id) > 64 or case_id in seen:
            raise ValueError("case_id must be unique non-empty bounded text")
        seen.add(case_id)
        lengths, target, limits = case.get("lengths"), case.get("target_xy"), case.get("limits")
        solvers = [("ccd", lambda: solve_ccd(lengths, target, limits,
                    initial_angles=case.get("initial_angles"), max_iterations=max_iterations,
                    tolerance=tolerance))]
        if type(lengths) in (tuple, list) and len(lengths) == 2:
            solvers.append(("two_link_analytic", lambda: solve_two_link(
                lengths, target, limits, elbow=case.get("elbow", "positive"), tolerance=tolerance)))
        for solver_name, run in solvers:
            tracemalloc.start()
            started = time.perf_counter_ns()
            result = run()
            elapsed = time.perf_counter_ns() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            rows.append(BenchmarkRow(case_id, solver_name, result.residual_m,
                result.iterations, result.evaluations, elapsed, peak,
                result.converged, result.digest_sha256))
    return tuple(rows)


def report_json(rows: Sequence[BenchmarkRow]) -> str:
    """Stable report serialization; volatile timing/memory are intentionally included as observations."""
    payload = {"schema": SCHEMA, "rows": [asdict(row) for row in rows],
               "notice": "Synthetic planar numerical benchmark only; not rigid-body physics or physical contact."}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    fixture = [{"case_id": "reachable-elbow", "lengths": [1.0, 1.0],
                "target_xy": [1.0, 1.0], "limits": [[-3.14, 3.14], [-3.14, 3.14]]}]
    print(report_json(run_benchmark(fixture)))
