"""Reproducible fixed-room diagnostic; no claim of generalized learning."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent


class PolicyEvaluationTests(unittest.TestCase):
    def test_deterministic_repeat_at_short_horizon(self):
        cmd = [sys.executable, str(ROOT / "examples/evaluate_point_room_policies.py"),
               "--ticks", "90"]
        left = json.loads(subprocess.check_output(cmd, text=True))
        right = json.loads(subprocess.check_output(cmd, text=True))
        self.assertEqual(left, right)
        self.assertEqual(left["physical_model"], "NONE_FIXTURE_ONLY")
        cases = left["controller_comparison"]
        self.assertEqual(set(cases), {"reactive_fixture", "learned_fixture"})
        self.assertEqual(cases["learned_fixture"]["learned_updates"], 90)
        self.assertEqual(cases["reactive_fixture"]["learned_updates"], 0)

    def test_full_horizon_metrics_report_both_controllers(self):
        cmd = [sys.executable, str(ROOT / "examples/evaluate_point_room_policies.py"),
               "--ticks", "350"]
        metrics = json.loads(subprocess.check_output(cmd, text=True))
        print("FIXTURE_POLICY_COMPARISON=" + json.dumps(metrics, sort_keys=True))
        cases = metrics["controller_comparison"]
        self.assertEqual(metrics["case"], "single_fixed_room_and_goal_not_generalizable")
        # Once the goal is reached, future frames are idle; they must
        # not invent rewards or additional training updates.
        expected_updates = cases["learned_fixture"]["first_goal_tick"] or 350
        self.assertEqual(cases["learned_fixture"]["learned_updates"], expected_updates)
        for result in cases.values():
            self.assertEqual(result["ticks"], 350)
            self.assertGreaterEqual(result["blocked_steps"], 0)
            self.assertLessEqual(result["blocked_steps"], 350)
            self.assertGreaterEqual(result["goal_L1_distance_m"], 0)
            self.assertLessEqual(result["goal_L1_distance_m"], 8)


if __name__ == "__main__":
    unittest.main()
