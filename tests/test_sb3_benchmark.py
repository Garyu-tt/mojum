"""Standard-library regression suite for opt-in SB3 benchmark #754."""
import contextlib
import io
import json
import math
import unittest
from unittest.mock import patch

from mojum import sb3_benchmark as bench


class ScenarioTests(unittest.TestCase):
    def test_splits_distinct_and_public(self):
        bench._validate_splits()
        train = {(bench.scenario(s).start, bench.scenario(s).goal) for s in bench.TRAIN_SEEDS}
        held = {(bench.scenario(s).start, bench.scenario(s).goal) for s in bench.HELDOUT_SEEDS}
        self.assertFalse(train & held)
        for seed in bench.TRAIN_SEEDS + bench.HELDOUT_SEEDS:
            s = bench.scenario(seed)
            self.assertEqual(s, bench.scenario(seed))
            self.assertEqual((s.room.width, s.room.height), (4.0, 4.0))
            self.assertGreaterEqual(math.dist(s.start, s.goal), 1.5)

    def test_reject_seed_bool_overflow_duplicates_and_train_leakage(self):
        for value in (True, -1, 2**32, 1.0, "11", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.scenario(value)
        with self.assertRaises(ValueError):
            bench._validate_splits(train_seeds=(11, 11))
        with self.assertRaises(ValueError):
            bench._validate_splits(heldout_seeds=(11, 13))

    def test_native_timestep_gate_before_external_import(self):
        for value in (0, 127, 129, 32769, True, 2.0, "128"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.run_native(timesteps=value)
        with self.assertRaises(ValueError):
            bench.run_native(timesteps=128, policy_seed=True)


class EvaluationTests(unittest.TestCase):
    def test_frozen_control_repeatable_and_bounded(self):
        first, second = bench.evaluate_frozen(), bench.evaluate_frozen()
        self.assertEqual(first, second)
        self.assertEqual(len(first["episodes"]), len(bench.HELDOUT_SEEDS))
        self.assertTrue(0 <= first["success_rate"] <= 1)
        for episode in first["episodes"]:
            self.assertLessEqual(episode["steps"], bench.MAX_STEPS)
            self.assertGreaterEqual(episode["blocked"], 0)

    def test_policy_gets_observation_not_simulator_state(self):
        observed = []
        def factory(seed):
            self.assertEqual(seed, bench.HELDOUT_SEEDS[0])
            def policy(obs):
                observed.append(tuple(sorted(obs)))
                self.assertNotIn("room", obs)
                self.assertNotIn("future", obs)
                return 0
            return policy
        result = bench._score_policy(factory, seeds=(bench.HELDOUT_SEEDS[0],))
        self.assertEqual(result["success_rate"], 0)
        self.assertTrue(observed)
        self.assertTrue(all(keys == ("blocked", "goal", "position", "tick") for keys in observed))

    def test_policy_fails_closed_on_invalid_actions(self):
        for value in (True, -1, 5, 1.25, float('nan'), None, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench._score_policy(lambda seed: lambda obs: value, seeds=(101,))

    def test_cli_default_stdlib_only_and_no_training_claim(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bench.main([]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema"], bench.SCHEMA)
        self.assertEqual(payload["mode"], "stdlib-frozen-only")
        self.assertNotIn("trained_ppo", payload)
        self.assertEqual(payload["frozen_no_learning"], bench.evaluate_frozen())

    def test_dependency_pin_fail_closed(self):
        with patch.object(bench.importlib.metadata, "version", side_effect=bench.importlib.metadata.PackageNotFoundError("missing")):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                bench.run_native(timesteps=128)
        with patch.object(bench.importlib.metadata, "version", return_value="999.0"):
            with self.assertRaisesRegex(RuntimeError, "expected"):
                bench.run_native(timesteps=128)


if __name__ == "__main__":
    unittest.main()
