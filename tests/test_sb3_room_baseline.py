"""Offline stdlib-only #690 SB3 benchmark checks."""
import json
import sys
import unittest
from unittest import mock

from mojum import sb3_room_baseline as b


class SB3BaselineTests(unittest.TestCase):
    def test_disjoint_scene_hashes(self):
        b.validate_split()
        a = {s.fingerprint() for s in b.TRAIN}
        c = {s.fingerprint() for s in b.HELDOUT}
        self.assertTrue(a.isdisjoint(c))
        self.assertEqual(len(c), 3)

    def test_idle_control(self):
        result = b.evaluate(b.idle_policy)
        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["total_steps"], 3 * b.MAX_STEPS)
        self.assertEqual(result["total_blocked"], 0)

    def test_frozen_greedy_wins_open_fails_walls(self):
        result = b.evaluate(b.greedy_policy)
        self.assertEqual(result["success_count"], 1)
        self.assertGreater(result["total_blocked"], 0)
        self.assertTrue(result["episodes"][0]["success"])
        self.assertFalse(result["episodes"][1]["success"])

    def test_reproducible_and_sanitized_report(self):
        a = b.report()
        self.assertEqual(a, b.report())
        self.assertEqual(a["schema"], b.SCHEMA)
        self.assertFalse(a["seed_changes_geometry"])
        self.assertNotIn("journal", json.dumps(a))
        self.assertNotIn("obstacles", json.dumps(a))

    def test_invalid_params(self):
        for seed in (-1, True, "7", 2**32):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                b.report(seed=seed)
        for steps in (0, 63, 4097, True, 64.0):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                b.report(timesteps=steps)
        with self.assertRaises(ValueError):
            b.report(mode="remote")
        # A max-u32 benchmark seed must not overflow on the third held-out room.
        self.assertEqual(b.report(seed=2**32 - 1)["seed"], 2**32 - 1)

    def test_invalid_policy_actions(self):
        for bad in (True, -1, 5, 0.5, None):
            with self.subTest(action=bad), self.assertRaises(ValueError):
                b.run_episode(b.HELDOUT[0], lambda _: bad, seed=1)

    def test_scene_leakage_fails(self):
        from dataclasses import replace
        with mock.patch.object(b, "HELDOUT", (
                replace(b.TRAIN[0], name="colliding-name"),)):
            with self.assertRaisesRegex(ValueError, "overlap"):
                b.validate_split()

    def test_control_import_isolation(self):
        with mock.patch.dict(sys.modules, {"stable_baselines3": None,
                                           "gymnasium": None}):
            self.assertEqual(b.report(mode="control")["mode"], "control")

    def test_sb3_missing_is_actionable(self):
        with mock.patch.dict(sys.modules, {"stable_baselines3": None}):
            with self.assertRaisesRegex(RuntimeError, "separately installed"):
                b.train_ppo()

    def test_training_checks_before_import(self):
        with self.assertRaises(ValueError):
            b.train_ppo(seed=1, timesteps=4097)
        with self.assertRaises(ValueError):
            b.train_ppo(seed=True)

    def test_cli_json_output(self):
        from io import StringIO
        with mock.patch("sys.stdout", new_callable=StringIO) as output:
            self.assertEqual(b.main(["--mode", "control"]), 0)
        self.assertEqual(json.loads(output.getvalue())["idle"]["success_count"], 0)


if __name__ == "__main__":
    unittest.main()
