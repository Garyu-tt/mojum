#!/usr/bin/env python3
"""Isolated CPU/stdlib PBD cloth control and Jolt soft-body fixture for #702.

This is NOT a Jolt runtime or physical body-contact proof. No network, hardware,
PRIVATE state, SQLite, or canonical Aquarium/GENESIS state is accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time

SCHEMA = "mojum.cloth-jolt-fixture/1"
REPORT_SCHEMA = "mojum.cloth-pbd-reference/1"
NATIVE_REPORT_SCHEMA = "mojum.cloth-jolt-native-report/1"
JOLT_VERSION = "5.7.0"
GRID = (12, 6)
SPACING = 0.05
DT = 0.002
RADIUS = 0.004
BALL_CENTER = (0.23, 0.0, 0.53)
BALL_RADIUS = 0.13
ANCHOR_ORIGIN = (0.0, -0.125, 1.05)
MAX_WORK = 2_000_000


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def fixture():
    """Public, synthetic, bounded common geometry; no external asset loading."""
    nx, ny = GRID
    vertices = []
    edges = []
    faces = []
    for i in range(nx):
        for j in range(ny):
            vertices.append({
                "position_m": [round(i * SPACING, 9),
                               round(ANCHOR_ORIGIN[1] + j * SPACING, 9),
                               ANCHOR_ORIGIN[2]],
                "inverse_mass": 0.0 if i == 0 else 1.0,
            })
            if i:
                edges.append([((i - 1) * ny + j), (i * ny + j)])
            if j:
                edges.append([i * ny + j - 1, i * ny + j])
            if i and j:
                a, b = (i - 1) * ny + j - 1, i * ny + j - 1
                c, d = (i - 1) * ny + j, i * ny + j
                faces.extend(([a, b, c], [b, d, c]))
    return {
        "schema": SCHEMA,
        "target_engine": "JoltPhysics",
        "target_version": JOLT_VERSION,
        "units": "metres_seconds",
        "source": "synthetic_public_fixture_only",
        "grid": list(GRID), "dt_seconds": DT,
        "vertex_radius_m": RADIUS,
        "vertices": vertices, "distance_constraints": edges,
        "triangles": faces,
        "anchor": {"locked_column": 0, "axis": "x",
                   "target_m": "0.045 * sin(2*pi*step/160)"},
        "colliders": {
            "plane_z_m": 0.0,
            "sphere_center_m": list(BALL_CENTER),
            "sphere_radius_m": BALL_RADIUS,
        },
        "self_collision": "disabled_for_reference",
        "hardware_contact": False,
    }


def fixture_hash(value=None):
    return hashlib.sha256(_canonical(fixture() if value is None else value)).hexdigest()


def _bounds(steps, warmup, iterations):
    if type(steps) is not int or not (1 <= steps <= 2000):
        raise ValueError("steps must be 1..2000")
    if type(warmup) is not int or not (0 <= warmup <= 500):
        raise ValueError("warmup must be 0..500")
    if type(iterations) is not int or not (1 <= iterations <= 32):
        raise ValueError("iterations must be 1..32")
    if (steps + warmup) * (GRID[0] * (GRID[1] - 1) +
                           (GRID[0] - 1) * GRID[1]) * iterations > MAX_WORK:
        raise ValueError("CPU work budget exceeded")


def _penetration(points):
    floor, sphere = 0.0, 0.0
    for x, y, z in points:
        if not all(math.isfinite(v) and abs(v) < 10000 for v in (x, y, z)):
            raise RuntimeError("nonfinite or unbounded cloth vertex")
        floor = max(floor, RADIUS - z)
        d = math.dist((x, y, z), BALL_CENTER)
        sphere = max(sphere, BALL_RADIUS + RADIUS - d)
    return max(0.0, floor), max(0.0, sphere)


def _project_contacts(point):
    if point[2] < RADIUS:
        point[2] = RADIUS
    delta = [point[k] - BALL_CENTER[k] for k in range(3)]
    dist = math.sqrt(sum(v * v for v in delta))
    target = BALL_RADIUS + RADIUS
    if dist < target:
        if dist < 1e-12:
            # An exactly centered vertex needs an explicit separating axis.
            point[2] += target
        else:
            for k in range(3):
                point[k] += delta[k] * ((target - dist) / dist)
    if point[2] < RADIUS:
        point[2] = RADIUS
    if not all(math.isfinite(x) and abs(x) < 10000 for x in point):
        raise RuntimeError("nonfinite or unbounded cloth vertex")


def run_reference(*, steps=100, warmup=10, iterations=6):
    """Verlet/PBD control; intentionally NOT an equivalent Jolt/MuJoCo solver."""
    _bounds(steps, warmup, iterations)
    spec = fixture()
    points = [list(v["position_m"]) for v in spec["vertices"]]
    previous = [p[:] for p in points]
    inv_mass = [v["inverse_mass"] for v in spec["vertices"]]
    edges = spec["distance_constraints"]
    durations = []
    max_floor, max_sphere = 0.0, 0.0
    max_anchor_drift = 0.0
    for step in range(steps + warmup):
        start = time.perf_counter_ns()
        target_x = 0.045 * math.sin(2 * math.pi * step / 160)
        for index, point in enumerate(points):
            if not inv_mass[index]:
                point[0] = target_x
                previous[index][:] = point
                continue
            old = point[:]
            for axis in (0, 1):
                point[axis] += (point[axis] - previous[index][axis]) * 0.995
            point[2] += (point[2] - previous[index][2]) * 0.995 - 9.81 * DT * DT
            previous[index] = old
        for _ in range(iterations):
            for a, b in edges:
                pa, pb = points[a], points[b]
                delta = [pb[k] - pa[k] for k in range(3)]
                length = math.sqrt(sum(v * v for v in delta))
                if length < 1e-12 or not math.isfinite(length):
                    raise RuntimeError("degenerate cloth constraint")
                weights = inv_mass[a] + inv_mass[b]
                if not weights:
                    continue
                correction = (length - SPACING) / (length * weights)
                for k in range(3):
                    change = delta[k] * correction
                    pa[k] += inv_mass[a] * change
                    pb[k] -= inv_mass[b] * change
            for index, point in enumerate(points):
                if inv_mass[index]:
                    _project_contacts(point)
        elapsed = time.perf_counter_ns() - start
        if step >= warmup:
            durations.append(elapsed)
            floor, sphere = _penetration(points)
            max_floor = max(max_floor, floor)
            max_sphere = max(max_sphere, sphere)
            max_anchor_drift = max(max_anchor_drift,
                max(abs(points[j][0] - target_x) for j in range(GRID[1])))
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_kib = int(rss / 1024) if sys.platform == "darwin" else int(rss)
    except ImportError:
        rss_kib = None
    return {
        "schema": REPORT_SCHEMA,
        "engine": "pbd_python_reference_not_jolt",
        "fixture_sha256": fixture_hash(spec),
        "steps": steps, "warmup_steps": warmup, "iterations": iterations,
        "vertices": len(points), "dt_seconds": DT,
        "final_positions_sha256": hashlib.sha256(_canonical(
            [[round(v, 9) for v in p] for p in points])).hexdigest(),
        "cpu_ms_per_step_p50": round(statistics.median(durations) / 1e6, 6),
        "cpu_ms_per_step_p95": round(
            sorted(durations)[math.ceil(0.95 * len(durations)) - 1] / 1e6, 6),
        "max_rss_kib_process": rss_kib,
        "max_anchor_drift_m": round(max_anchor_drift, 9),
        "max_floor_vertex_penetration_m_approx": round(max_floor, 9),
        "max_sphere_vertex_penetration_m_approx": round(max_sphere, 9),
        "contact_proof": False,
        "notes": "Headless synthetic projection baseline; no self-contact or measured Jolt runtime",
    }


def validate_native_report(value, *, expected_steps, expected_warmup):
    """Validate a proposed Jolt report; this does NOT attest its provenance."""
    required = {"schema", "engine", "engine_version", "fixture_sha256",
                "steps", "warmup_steps", "vertices", "dt_seconds",
                "cpu_ms_per_step_p50", "cpu_ms_per_step_p95",
                "max_rss_kib_process", "max_anchor_drift_m",
                "max_floor_vertex_penetration_m_approx",
                "max_sphere_vertex_penetration_m_approx",
                "hardware_contact"}
    if type(value) is not dict or set(value) != required:
        raise ValueError("native Jolt report must have exact allowlisted fields")
    if (value["schema"] != NATIVE_REPORT_SCHEMA or
        value["engine"] != "JoltPhysics" or
        value["engine_version"] != JOLT_VERSION or
        value["fixture_sha256"] != fixture_hash() or
        type(value["steps"]) is not int or value["steps"] != expected_steps or
        type(value["warmup_steps"]) is not int or
        value["warmup_steps"] != expected_warmup or
        type(value["vertices"]) is not int or
        value["vertices"] != GRID[0] * GRID[1] or
        type(value["dt_seconds"]) is not float or
        value["dt_seconds"] != DT or value["hardware_contact"] is not False):
        raise ValueError("native report identity/shape mismatch")
    for name in ("cpu_ms_per_step_p50", "cpu_ms_per_step_p95",
                 "max_anchor_drift_m", "max_floor_vertex_penetration_m_approx",
                 "max_sphere_vertex_penetration_m_approx"):
        x = value[name]
        if type(x) not in (float, int) or not math.isfinite(x) or x < 0:
            raise ValueError("invalid native measurement: " + name)
    rss = value["max_rss_kib_process"]
    if rss is not None and (type(rss) is not int or rss < 0):
        raise ValueError("invalid native RSS")
    if value["cpu_ms_per_step_p95"] < value["cpu_ms_per_step_p50"]:
        raise ValueError("invalid native timing quantiles")
    return dict(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true",
                        help="print synthetic Jolt handoff fixture (no engine run)")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        _bounds(args.steps, args.warmup, args.iterations)
        result = fixture() if args.fixture else run_reference(
            steps=args.steps, warmup=args.warmup, iterations=args.iterations)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
