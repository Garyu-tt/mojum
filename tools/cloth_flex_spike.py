#!/usr/bin/env python3
"""Optional, headless MuJoCo flex cloth spike. No Aquarium integration.

The default command benchmarks a actuated-edge cloth patch under gravity.
``--print-mjcf`` works without MuJoCo; simulation requires the opt-in package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import xml.etree.ElementTree as ET

DT = 0.002
FLOOR_Z = 0.0
BALL_CENTER = (0.23, 0.0, 0.53)
BALL_RADIUS = 0.13
GRID = (12, 6)
VERTEX_RADIUS = 0.004


def make_mjcf(*, selfcollide: str = "auto", passive: bool = True) -> str:
    """Generate fixed-size, self-contained MJCF; never load external assets."""
    if selfcollide not in ("auto", "none"):
        raise ValueError("selfcollide must be 'auto' or 'none'")
    if type(passive) is not bool:
        raise ValueError("passive must be boolean")
    return f'''<mujoco model="mojum_cloth_cpu_spike">
  <option timestep="{DT}" solver="CG" tolerance="1e-6"
          iterations="250" integrator="discrete" gravity="0 0 -9.81"/>
  <size memory="32M"/>
  <worldbody>
    <geom name="floor" type="plane" size="2 2 .1" pos="0 0 {FLOOR_Z}"/>
    <geom name="collision_ball" type="sphere" size="{BALL_RADIUS}"
          pos="{BALL_CENTER[0]} {BALL_CENTER[1]} {BALL_CENTER[2]}"/>
    <body name="cloth_anchor" pos="0 -.125 1.05">
      <joint name="anchor_slide" type="slide" axis="1 0 0" limited="true" range="-.1 .1" damping="10"/>
      <inertial pos="0 0 0" mass=".05" diaginertia=".001 .001 .001"/>
      <flexcomp name="scarf" type="grid" dim="2" count="{GRID[0]} {GRID[1]} 1"
                spacing=".05 .05 .05" mass=".14" radius="{VERTEX_RADIUS}">
        <pin gridrange="0 0 0 {GRID[1]-1}"/>
        <contact selfcollide="{selfcollide}" passive="{str(passive).lower()}"
                 solref="0.01 1" solimp=".95 .99 .0001"/>
        <elasticity young="2e4" poisson=".2" thickness=".001"
                    elastic2d="both" damping=".01"/>
      </flexcomp>
    </body>
  </worldbody>
  <actuator>
    <position name="anchor_driver" joint="anchor_slide" kp="100" kv="10"/>
  </actuator>
</mujoco>
'''


def _percentile_ms(samples_ns: list[int], pct: float) -> float:
    if not samples_ns:
        raise ValueError("empty timings")
    rank = max(0, math.ceil(pct * len(samples_ns)) - 1)
    return sorted(samples_ns)[rank] / 1_000_000


def _vertex_penetrations(data: object) -> tuple[float | None, float | None]:
    """Approximate vertex/plane and vertex/sphere penetration, NOT full contact proof."""
    points = getattr(data, "flexvert_xpos", None)
    if points is None:
        return None, None
    floor = 0.0
    sphere = 0.0
    for x, y, z in points:
        floor = max(floor, FLOOR_Z + VERTEX_RADIUS - float(z))
        distance = math.sqrt(sum((float(a) - b)**2 for a, b in
                                 zip((x, y, z), BALL_CENTER)))
        sphere = max(sphere, BALL_RADIUS + VERTEX_RADIUS - distance)
    return floor, sphere


def benchmark(mjcf: str, *, steps: int, warmup: int) -> dict:
    if not 1 <= steps <= 20_000 or not 0 <= warmup <= 2_000:
        raise ValueError("steps 1..20000 and warmup 0..2000 required")
    try:
        import mujoco  # type: ignore[import-not-found]  # opt-in dependency
    except ImportError as exc:
        raise RuntimeError("MuJoCo is optional and not installed; use --print-mjcf or install mujoco separately") from exc

    model = mujoco.MjModel.from_xml_string(mjcf)
    data = mujoco.MjData(model)
    if model.nmocap != 0 or model.nflex != 1 or model.nu != 1:
        raise RuntimeError("unexpected model topology: require one actuated slide anchor and one flex")
    timings = []
    max_floor = 0.0
    max_sphere = 0.0
    measured = True
    for i in range(warmup + steps):
        # Deterministic, bounded movement of the pinned edge in the same world.
        data.ctrl[0] = .045 * math.sin(2 * math.pi * i / 160)
        start = time.perf_counter_ns()
        mujoco.mj_step(model, data)
        elapsed = time.perf_counter_ns() - start
        if i >= warmup:
            timings.append(elapsed)
            floor, sphere = _vertex_penetrations(data)
            if floor is None or sphere is None:
                measured = False
            else:
                max_floor = max(max_floor, floor)
                max_sphere = max(max_sphere, sphere)
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux returns KiB; macOS returns bytes. No portable Windows API.
        rss_kib = int(rss / 1024) if sys.platform == "darwin" else int(rss)
    except ImportError:
        rss_kib = None
    return {
        "schema": "mojum.cloth-spike-benchmark/1",
        "engine": "mujoco", "engine_version": mujoco.__version__,
        "mjcf_sha256": hashlib.sha256(mjcf.encode("utf-8")).hexdigest(),
        "steps": steps, "warmup_steps": warmup, "dt_seconds": DT,
        "vertices": int(model.nflexvert), "flexes": int(model.nflex),
        "cpu_ms_per_step_p50": round(statistics.median(timings) / 1_000_000, 6),
        "cpu_ms_per_step_p95": round(_percentile_ms(timings, .95), 6),
        "max_rss_kib_process": rss_kib,
        "max_floor_vertex_penetration_m_approx": round(max_floor, 6) if measured else None,
        "max_sphere_vertex_penetration_m_approx": round(max_sphere, 6) if measured else None,
        "penetration_metric": "vertex_center_plus_radius_only_not_mesh_surface_or_contact_proof",
        "notes": "One-process headless CPU wall timing, not realtime or production evidence",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selfcollide", choices=("auto", "none"), default="auto")
    parser.add_argument("--passive", choices=("true", "false"), default="true")
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--print-mjcf", action="store_true", help="print XML; no MuJoCo required")
    args = parser.parse_args(argv)
    if not 1 <= args.steps <= 20_000 or not 0 <= args.warmup <= 2_000:
        parser.error("steps must be 1..20000; warmup must be 0..2000")
    mjcf = make_mjcf(selfcollide=args.selfcollide, passive=args.passive == "true")
    # Parse XML even for dry-run, catching accidental malformed template edits.
    ET.fromstring(mjcf)
    if args.print_mjcf:
        print(mjcf, end="")
        return 0
    try:
        result = benchmark(mjcf, steps=args.steps, warmup=args.warmup)
    except (RuntimeError, ValueError) as exc:
        print(f"cloth spike unavailable: {exc}", file=sys.stderr)
        return 2
    result["settings"] = {"selfcollide": args.selfcollide, "passive": args.passive == "true"}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
