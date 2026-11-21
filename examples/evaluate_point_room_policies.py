"""Deterministic baseline comparison for Mojum's synthetic point-room *fixture*.

This benchmark does NOT measure intelligence, MuJoCo mobility, navigation
quality across arbitrary rooms, or statistical reliability. It only compares
a fixed-seed hard-coded R0.4 controller against a fixed-seed R0.5 reward
learner in the SAME point-room and goal for a bounded number of ticks.
"""

import argparse
import json
from pathlib import Path
import tempfile

from mojum.aquarium import Aquarium


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def evaluate(ticks=350):
    if type(ticks) is not int or not 1 <= ticks <= 600:
        raise ValueError("evaluation ticks must be within 1..600")
    results = {}
    with tempfile.TemporaryDirectory(prefix="mojum-fixture-benchmark-") as tmp:
        for mode in ("reactive_fixture", "learned_fixture"):
            db = Path(tmp) / (mode + ".sqlite3")
            with Aquarium(db, SEED, GRAPH, control_mode=mode) as a:
                total_blocked = 0
                first_goal_tick = None
                total_reward = 0.0
                for tick in range(1, ticks + 1):
                    frame = a.step()
                    total_blocked += int(frame["world"]["blocked"])
                    if mode == "learned_fixture":
                        total_reward += frame["policy_learning"]["reward"] or 0.0
                    if first_goal_tick is None and abs(a.world.x - a.goal[0]) < 1e-9 and abs(a.world.y - a.goal[1]) < 1e-9:
                        first_goal_tick = tick
                results[mode] = {
                    "ticks": ticks,
                    "blocked_steps": total_blocked,
                    "goal_L1_distance_m": round(abs(a.world.x - a.goal[0]) + abs(a.world.y - a.goal[1]), 9),
                    "first_goal_tick": first_goal_tick,
                    "learned_updates": a.policy.updates if a.policy is not None else 0,
                    "total_reward": round(total_reward, 8) if mode == "learned_fixture" else None,
                }
    return {
        "provenance": "mojum.synthetic-point-room-evaluation/1",
        "case": "single_fixed_room_and_goal_not_generalizable",
        "physical_model": "NONE_FIXTURE_ONLY",
        "controller_comparison": results,
    }


def main():
    parser = argparse.ArgumentParser(description="R0.4 vs R0.5 synthetic fixture comparison")
    parser.add_argument("--ticks", type=int, default=350)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.ticks), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
