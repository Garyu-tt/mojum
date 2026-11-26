"""Offline only: no imported RoboPianist, numpy, MuJoCo or hardware access."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mojum.robopianist_bridge import (
    FRAME_SCHEMA, REPORT_SCHEMA, MAX_STEPS, PianoBridge, PianoBridgeError,
    SyntheticPianoEnv, compare_fixture, fixture_trial, open_robopianist,
)


class PianoBridgeTests(unittest.TestCase):
    def make(self, seed=7, steps=4):
        env = SyntheticPianoEnv(seed, steps=steps)
        return env, PianoBridge(env, piano_keys=12, origin="synthetic_public",
                                array_factory=tuple)

    def test_reproducible_held_out_comparison(self):
        a, b = compare_fixture(), compare_fixture()
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], REPORT_SCHEMA)
        self.assertFalse(a["learned_skill_measured"])
        self.assertFalse(a["physical_piano_measured"])
        self.assertEqual({t["mode"] for t in a["train"]}, {"scripted_target", "frozen_zero"})
        for trials in (a["train"], a["held_out"]):
            self.assertEqual(trials[0]["mean_reward"], 1.0)
            self.assertGreater(trials[0]["mean_reward"], trials[1]["mean_reward"])
        self.assertNotEqual(a["train"][0]["trace_sha256"], a["held_out"][0]["trace_sha256"])
        self.assertLess(len(json.dumps(a)), 2048)

    def test_step_goal_reward_and_terminal(self):
        env, bridge = self.make(steps=2)
        zero = [0.0] * 12
        start = bridge.reset()
        self.assertEqual(start["schema"], FRAME_SCHEMA)
        self.assertIsNone(start["reward"])
        self.assertTrue(start["virtual_only"])
        self.assertEqual(start["target_keys"], list(env.targets[0]))
        one, two = bridge.step(zero), bridge.step(zero)
        self.assertEqual((one["tick"], two["tick"]), (1, 2))
        self.assertFalse(one["terminal"])
        self.assertTrue(two["terminal"])
        self.assertEqual(env.step_calls, 2)
        with self.assertRaises(PianoBridgeError):
            bridge.step(zero)
        self.assertEqual(env.step_calls, 2)

    def test_invalid_action_is_not_dispatched(self):
        env, bridge = self.make()
        bridge.reset()
        invalid = ([True] * 12, [0.0] * 11, [2.0] * 12,
                   [float("nan")] * 12, [float("inf")] * 12,
                   "not a vector", ["0"] * 12)
        for bad in invalid:
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(PianoBridgeError):
                bridge.step(bad)
        self.assertEqual(env.step_calls, 0)
        self.assertEqual(bridge.step([0.0] * 12)["tick"], 1)

    def test_post_cast_action_validation_prevents_backend_dispatch(self):
        # An optional array_factory / dtype conversion can turn a previously
        # validated finite vector into NaN, infinity, out-of-spec, or a wrong
        # shape. The exact native vector must be checked before env.step().
        for altered in (float("nan"), float("inf"), -5.0, 5.0):
            with self.subTest(altered=altered):
                env = SyntheticPianoEnv(7, steps=2)
                bridge = PianoBridge(
                    env, piano_keys=env.keys, array_factory=lambda vector, x=altered:
                    (x,) + tuple(vector[1:]))
                bridge.reset()
                with self.assertRaises(PianoBridgeError):
                    bridge.step([0.0] * env.keys)
                self.assertEqual(env.step_calls, 0)
                with self.assertRaises(PianoBridgeError):
                    bridge.step([0.0] * env.keys)

        env = SyntheticPianoEnv(7, steps=2)
        bridge = PianoBridge(env, piano_keys=env.keys,
                             array_factory=lambda vector: tuple(vector[:-1]))
        bridge.reset()
        with self.assertRaises(PianoBridgeError):
            bridge.step([0.0] * env.keys)
        self.assertEqual(env.step_calls, 0)

    def test_valid_native_factory_action_dispatches_once(self):
        env = SyntheticPianoEnv(11, steps=2)
        bridge = PianoBridge(env, piano_keys=env.keys,
                             array_factory=lambda vector: tuple(float(v) for v in vector))
        bridge.reset()
        self.assertEqual(bridge.step([0.0] * env.keys)["tick"], 1)
        self.assertEqual(env.step_calls, 1)

    def test_out_of_bounds_spec_and_parameter_validation(self):
        for kw in ({"seed": True}, {"seed": -1}, {"seed": 2**32},
                   {"seed": 2, "steps": MAX_STEPS + 1}, {"seed": 1, "keys": 0}):
            with self.subTest(kw=kw), self.assertRaises(PianoBridgeError):
                SyntheticPianoEnv(**kw)
        env, _ = self.make()
        with self.assertRaises(PianoBridgeError):
            PianoBridge(env, piano_keys=True)
        with self.assertRaises(PianoBridgeError):
            PianoBridge(env, piano_keys=89)
        with self.assertRaises(PianoBridgeError):
            PianoBridge(env, origin="private host token")
        with self.assertRaises(PianoBridgeError):
            compare_fixture(1, 1)
        with self.assertRaises(PianoBridgeError):
            fixture_trial(1, "not supported")

    def test_incompatible_spec_rejected_before_reset(self):
        env, _ = self.make()
        for spec in (SimpleNamespace(shape=(257,), minimum=-1, maximum=1),
                     SimpleNamespace(shape=(12, 1), minimum=-1, maximum=1),
                     SimpleNamespace(shape=(12,), minimum=2, maximum=1),
                     SimpleNamespace(shape=(12,), minimum=float("nan"), maximum=1)):
            env.action_spec = lambda spec=spec: spec
            with self.assertRaises(PianoBridgeError):
                PianoBridge(env, piano_keys=12)
        self.assertEqual(env.step_calls, 0)

    def test_poisoned_on_backend_step_failure_no_retry(self):
        env, bridge = self.make()
        bridge.reset()
        def fail(_):
            env.step_calls += 1
            raise RuntimeError("secret backend token: do not leak")
        env.step = fail
        with self.assertRaisesRegex(PianoBridgeError, "backend step failed") as e:
            bridge.step([0.0] * 12)
        self.assertNotIn("secret", str(e.exception))
        with self.assertRaises(PianoBridgeError):
            bridge.step([0.0] * 12)
        with self.assertRaises(PianoBridgeError):
            bridge.reset()
        self.assertEqual(env.step_calls, 1)

    def test_poisoned_if_observation_bad_after_backend_step(self):
        env, bridge = self.make()
        bridge.reset()
        original = env.step
        def altered(action):
            t = original(action)
            return SimpleNamespace(observation={"goal": [float("nan")] * 12},
                                   reward=t.reward, discount=t.discount,
                                   last=t.last)
        env.step = altered
        with self.assertRaises(PianoBridgeError):
            bridge.step([0.0] * 12)
        with self.assertRaises(PianoBridgeError):
            bridge.step([0.0] * 12)
        self.assertEqual(env.step_calls, 1)

    def test_goal_budget_and_raw_observation_redaction(self):
        env, bridge = self.make()
        original = env.reset
        def reset():
            ts = original()
            ts.observation["private_memory"] = "DO_NOT_EXPOSE"
            return ts
        env.reset = reset
        frame = bridge.reset()
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(frame))
        env, bridge = self.make()
        env.reset = lambda: SimpleNamespace(observation={"goal": [0.0]*1500},
                                            reward=None, discount=None, last=lambda: False)
        with self.assertRaises(PianoBridgeError):
            bridge.reset()

    def test_synthetic_needs_no_external_apis_or_files(self):
        with patch("builtins.open", side_effect=AssertionError("I/O forbidden")), \
             patch("socket.socket", side_effect=AssertionError("network forbidden")):
            report = compare_fixture()
        self.assertEqual(report["origin"], "synthetic_public_only")

    def test_no_implicit_backend_import_or_task_download(self):
        with patch("importlib.import_module", side_effect=AssertionError("no import")):
            compare_fixture()
            with self.assertRaises(PianoBridgeError):
                open_robopianist("../../private")

    def test_explicit_real_env_boundary_can_be_injected(self):
        env, _ = self.make()
        bridge = PianoBridge(env, piano_keys=env.keys, array_factory=tuple)
        bridge.reset()
        result = bridge.step([0.0] * 12)
        self.assertEqual(result["origin"], "injected_environment")
        self.assertEqual(result["tick"], 1)

    def test_reset_required_and_reset_allowed_after_clean_episode(self):
        env, bridge = self.make(steps=1)
        with self.assertRaises(PianoBridgeError):
            bridge.step([0.0] * 12)
        bridge.reset()
        bridge.step([0.0] * 12)
        bridge.reset()
        self.assertEqual(bridge.step([0.0] * 12)["tick"], 1)


if __name__ == "__main__":
    unittest.main()
