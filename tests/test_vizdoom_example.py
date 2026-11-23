import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "vizdoom" / "offline_navigation.py"
SPEC = importlib.util.spec_from_file_location("mojum_vizdoom_offline_navigation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OfflineNavigationTests(unittest.TestCase):
    def test_seeded_episode_is_replayable(self):
        self.assertEqual(MODULE.run_episode(seed=17), MODULE.run_episode(seed=17))
        self.assertTrue(MODULE.run_episode(seed=17)["success"])

    def test_different_seeds_change_observation(self):
        first = MODULE.OfflineNavigationEnv().reset(seed=1)
        second = MODULE.OfflineNavigationEnv().reset(seed=2)
        self.assertNotEqual(first, second)

    def test_frozen_baseline_reaches_goal_within_budget(self):
        result = MODULE.run_episode(seed=42, max_steps=32)
        self.assertTrue(result["success"])
        self.assertLessEqual(result["steps"], 32)
        self.assertEqual(result["trace"][-1]["reward"], 10)

    def test_wait_control_truncates_without_success(self):
        result = MODULE.run_episode(seed=9, max_steps=3, policy="wait")
        self.assertFalse(result["success"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["steps"], 3)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.OfflineNavigationEnv().step("FIRE")

    def test_step_after_terminal_requires_reset(self):
        env = MODULE.OfflineNavigationEnv(max_steps=1)
        env.reset(seed=0)
        env.step("UP")
        with self.assertRaises(RuntimeError):
            env.step("UP")

    def test_seed_and_budget_are_strictly_bounded(self):
        with self.assertRaises(ValueError):
            MODULE.OfflineNavigationEnv().reset(seed=True)
        with self.assertRaises(ValueError):
            MODULE.OfflineNavigationEnv(max_steps=0)

    def test_generated_mazes_have_a_path(self):
        for seed in range(100):
            env = MODULE.OfflineNavigationEnv()
            env.reset(seed=seed)
            self.assertLessEqual(len(MODULE.shortest_path(env)), 12)

    def test_optional_import_has_clear_opt_in_failure(self):
        try:
            MODULE.require_vizdoom()
        except RuntimeError as exc:
            self.assertIn("Install it separately", str(exc))


if __name__ == "__main__":
    unittest.main()
