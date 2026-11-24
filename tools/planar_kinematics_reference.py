#!/usr/bin/env python3
"""Print a deterministic planar FK/IK reference sample as JSON."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mojum.planar_kinematics_reference import Point2, solve_both_branches

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--link1-m", type=float, default=0.7)
    parser.add_argument("--link2-m", type=float, default=0.4)
    parser.add_argument("--x-m", type=float, default=0.8)
    parser.add_argument("--y-m", type=float, default=0.5)
    a = parser.parse_args()
    solutions = solve_both_branches(a.link1_m, a.link2_m, Point2(a.x_m, a.y_m))
    print(json.dumps({
        "schema": "mojum.planar-kinematics-reference/1",
        "units": {"length": "m", "angle": "rad"},
        "target_m": {"x": a.x_m, "y": a.y_m},
        "reachable": bool(solutions),
        "solutions": [{"branch": s.branch, "shoulder_rad": s.joints.shoulder,
                       "elbow_rad": s.joints.elbow,
                       "achieved_m": {"x": s.position.x, "y": s.position.y},
                       "residual_m": s.residual_m} for s in solutions]
    }, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
