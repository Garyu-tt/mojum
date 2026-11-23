"""No third-party packages needed; no real inference or private state."""
import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from mojum.sb3_room_benchmark import (
    EVAL_SCENES, TRAIN_SCENES, EPISODE_STEPS, SCHEMA,
    _discrete_action, evaluate, main, plan, run, validate_config,
)
from mojum.gym_room import PointRoomEpisode


class FrozenMoveEast:
    def predict(self, observation, deterministic=False):
        assert deterministic
        return 1, None


class TestSb3Benchmark(unittest.TestCase):
    def test_heldout_disjoint_geometry_and_fixed_budget(self):
        p = plan()
        self.assertEqual(p["schema"], SCHEMA)
        self.assertFalse(p["run_requested"])
        self.assertEqual(len(set(x["sha256"] for x in p["training_scenes"] + p["heldout_scenes"])), 8)
        self.assertEqual(p["parallel_envs"], 4)
        self.assertEqual(p["max_episode_steps"], EPISODE_STEPS)

    def test_bad_config_before_importing_heavy_dependencies(self):
        for bad in (0, 255, 257, 8193, -256, True, 2048.0, "2048"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config(bad, 42)
        for bad in (-1, 2**32, True, None, 4.0):
            with self.subTest(seed=bad), self.assertRaises(ValueError):
                validate_config(256, bad)

    def test_invalid_predictions_fail_closed(self):
        for bad in (-1, 5, True, 1.2, "1", None, [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _discrete_action(bad)
        self.assertEqual(_discrete_action(0), 0)
        self.assertEqual(_discrete_action(4), 4)

    def test_frozen_determinism_stdlib_no_training(self):
        def factory(scene):
            return PointRoomEpisode(scene.room(), start=(0.5, 0.5), goal=(3.5, 3.5),
                                    step_m=0.5, goal_radius_m=0.26, max_steps=EPISODE_STEPS)
        first = evaluate(FrozenMoveEast(), env_factory=factory)
        self.assertEqual(first, evaluate(FrozenMoveEast(), env_factory=factory))
        self.assertEqual(len(first["episodes"]), len(EVAL_SCENES))
        self.assertTrue(all(0 < e["steps"] <= EPISODE_STEPS for e in first["episodes"]))
        self.assertTrue(all(not e["success"] for e in first["episodes"]))
        self.assertTrue(all(e["blocked_steps"] >= 0 for e in first["episodes"]))

    def test_evaluation_rejects_scenes_duplicated_and_bad_seed(self):
        with self.assertRaises(ValueError):
            evaluate(FrozenMoveEast(), scenes=EVAL_SCENES + EVAL_SCENES)
        with self.assertRaises(ValueError):
            evaluate(FrozenMoveEast(), seed=True)

    def test_cli_plan_does_not_load_optional_packages(self):
        buf = io.StringIO()
        with mock.patch.dict(sys.modules, {"stable_baselines3": None, "gymnasium": None}):
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["--timesteps", "256"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["timesteps"], 256)

    def test_missing_sb3_fails_closed_after_explicit_run(self):
        with mock.patch.dict(sys.modules, {"stable_baselines3": None}):
            with self.assertRaisesRegex(RuntimeError, "opt-in"):
                run(256)


if __name__ == "__main__":
    unittest.main()
