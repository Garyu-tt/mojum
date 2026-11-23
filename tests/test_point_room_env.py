"""CPU/offline acceptance for the optional goal-free point-room facade."""
import unittest

from mojum.point_room_env import PointRoomEnv
from mojum.world_room import Room


class PointRoomEnvTests(unittest.TestCase):
    def make_env(self, **kwargs):
        return PointRoomEnv(Room(4, 4, ((2, 1, 0.1, 2),)), start=(1, 2), **kwargs)

    def test_reset_then_step_shape_and_no_false_physics(self):
        env = self.make_env(max_steps=2)
        obs, info = env.reset(seed=5)
        self.assertEqual((obs["tick"], obs["x"], obs["y"]), (0, 1, 2))
        self.assertTrue(obs["synthetic"])
        self.assertFalse(info["physical_contact"])
        next_obs, reward, terminated, truncated, meta = env.step((0.25, 0))
        self.assertEqual((next_obs["tick"], next_obs["x"]), (1, 1.25))
        self.assertEqual(reward, 0.0)
        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertFalse(meta["physical_contact"])
        self.assertFalse(meta["blocked"])

    def test_collision_and_truncation(self):
        env = self.make_env(max_steps=1)
        env.reset()
        obs, _, terminated, truncated, info = env.step((0.0, 0.0))
        self.assertFalse(info["blocked"])
        self.assertTrue(truncated)
        self.assertFalse(terminated)
        self.assertEqual(obs["tick"], 1)
        with self.assertRaises(RuntimeError):
            env.step((0.0, 0.0))
        fresh, _ = env.reset()
        self.assertEqual(fresh["tick"], 0)

    def test_collision_not_exposed_as_hardware(self):
        env = PointRoomEnv(Room(2, 2, ((1, 0.5, 0.1, 1),)), start=(0.9, 1))
        env.reset()
        obs, _, _, _, info = env.step((0.25, 0.0))
        self.assertEqual((obs["x"], obs["y"]), (0.9, 1))
        self.assertTrue(info["blocked"])
        self.assertFalse(info["physical_contact"])

    def test_repeated_reset_trajectory_is_deterministic(self):
        env = self.make_env()
        trajectories = []
        for seed in (7, 99):
            env.reset(seed=seed)
            trajectories.append([env.step((0.1, 0.0))[0] for _ in range(3)])
        self.assertEqual(trajectories[0], trajectories[1])

    def test_large_nonnegative_seed_is_accepted_and_replayed(self):
        for seed in (2**32, 2**64 + 17, 2**128):
            with self.subTest(seed=seed):
                env = self.make_env()
                initial = env.reset(seed=seed)
                self.assertEqual(initial[1]["seed"], seed)
                action = env.step((0.1, 0.0))
                self.assertEqual(env.reset(seed=seed), initial)
                self.assertEqual(env.step((0.1, 0.0)), action)

    def test_invalid_action_never_advances_state(self):
        env = self.make_env()
        first, _ = env.reset()
        for bad in ((float("nan"), 0), (float("inf"), 0),
                    (True, 0), ("1", 0), (10**1000, 0), (0.3, 0), (0.2, 0.2), [0], "x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    env.step(bad)
        observed, _, _, _, _ = env.step((0, 0))
        self.assertEqual(observed["tick"], first["tick"] + 1)

    def test_empty_options_and_strict_seed(self):
        env = self.make_env()
        for bad_seed in (True, -1, 1.5, "5"):
            with self.assertRaises(ValueError):
                env.reset(seed=bad_seed)
        for options in ({"unknown": 1}, [], ""):
            with self.assertRaises(ValueError):
                env.reset(options=options)
        env.reset(options={})

    def test_invalid_init(self):
        for bad in (True, 0, 100001):
            with self.assertRaises(ValueError):
                self.make_env(max_steps=bad)
        for bad in (True, float("nan"), 0, 0.250001, 0.5, 1, 2):
            with self.assertRaises(ValueError):
                self.make_env(max_step_m=bad)
        with self.assertRaises(ValueError):
            PointRoomEnv(Room(4, 4), start=(True, 1))

    def test_hard_cap_is_not_bypassed_by_mutating_public_budget(self):
        env = self.make_env(max_step_m=0.125)
        env.reset()
        with self.assertRaises(ValueError):
            env.step((0.2, 0))
        env.max_step_m = 1.0
        with self.assertRaises(ValueError):
            env.step((0.5, 0))
        env.max_step_m = float("nan")
        with self.assertRaises(ValueError):
            env.step((0.1, 0))
        env.max_step_m = 0.25
        with self.assertRaises(ValueError):
            env.step((0.25, 0))  # Cannot raise the constructor 0.125 m cap.
        env.max_step_m = 0.125
        observed, _, _, _, _ = env.step((0.125, 0))
        self.assertEqual(observed["tick"], 1)

    def test_mutating_public_episode_budget_cannot_extend_original_limit(self):
        env = self.make_env(max_steps=1)
        env.reset()
        for bad in (2, 100001, True, -1, 1.5):
            with self.subTest(budget=bad):
                env.max_steps = bad
                with self.assertRaisesRegex(ValueError, "episode step budget"):
                    env.step((0, 0))
        env.max_steps = 1
        observed, _, _, truncated, _ = env.step((0, 0))
        self.assertEqual(observed["tick"], 1)
        self.assertTrue(truncated)
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_step_before_reset_refused(self):
        with self.assertRaises(RuntimeError):
            self.make_env().step((0, 0))


if __name__ == "__main__":
    unittest.main()
