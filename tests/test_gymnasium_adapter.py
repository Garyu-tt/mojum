"""Optional actual-Gymnasium smoke test; stdlib baseline never imports it."""
import importlib.util
import unittest

from mojum.gymnasium_adapter import make_gymnasium_point_room
from mojum.world_room import Room


HAS_GYM = (importlib.util.find_spec("gymnasium") is not None
           and importlib.util.find_spec("numpy") is not None)


@unittest.skipUnless(HAS_GYM, "install gymnasium+numpy for optional integration")
class GymnasiumPointRoomTests(unittest.TestCase):
    def test_actual_spaces_and_baseline(self):
        import gymnasium as gym
        env = make_gymnasium_point_room(
            Room(2, 2, ((1.0, 0.5, 0.1, 1.0),)),
            start=(0.5, 1.0), max_steps=3,
        )
        self.assertIsInstance(env, gym.Env)
        obs, info = env.reset(seed=13)
        self.assertTrue(env.observation_space.contains(obs))
        self.assertEqual(info["schema"], "mojum.point-room-episode/1")
        self.assertFalse(info["physical_contact"])
        for _ in range(3):
            action = env.action_space.sample()
            obs, reward, terminated, truncated, meta = env.step(action)
            self.assertTrue(env.observation_space.contains(obs))
            self.assertEqual(reward, 0.0)
            self.assertFalse(terminated)
            self.assertFalse(meta["physical_contact"])
        self.assertTrue(truncated)
        env.close()

    def test_large_nonnegative_seed_preserves_gymnasium_contract(self):
        env = make_gymnasium_point_room(Room(4, 4), start=(1, 1))
        for seed in (2**32, 2**64 + 17, 2**128):
            with self.subTest(seed=seed):
                observation, info = env.reset(seed=seed)
                self.assertEqual(info["seed"], seed)
                self.assertTrue(env.observation_space.contains(observation))
                first, _, _, _, _ = env.step([0.1, 0.0])
                second_reset, second_info = env.reset(seed=seed)
                self.assertEqual(second_info["seed"], seed)
                self.assertTrue(env.observation_space.contains(second_reset))
                second, _, _, _, _ = env.step([0.1, 0.0])
                self.assertTrue((first["position"] == second["position"]).all())
        env.close()

    def test_float64_just_outside_box_is_rejected_before_rounding(self):
        # Previously step() cast to float32 before Box.contains() and
        # silently accepted an out-of-range float64 right at the boundary.
        import math
        import numpy as np
        env = make_gymnasium_point_room(Room(4, 4), start=(1, 1))
        env.reset(seed=0)
        upper = float(env.action_space.high[0])
        just_outside = math.nextafter(upper, math.inf)
        self.assertGreater(just_outside, upper)
        self.assertEqual(float(np.float32(just_outside)), upper)
        self.assertLess(just_outside, 0.25)
        with self.assertRaisesRegex(ValueError, "outside the advertised"):
            env.step([just_outside, 0.0])
        obs, _, _, _, _ = env.step([0.0, 0.0])
        self.assertEqual(obs["tick"], 1)
        env.close()

    def test_seed_is_deterministic_for_fixture(self):
        env = make_gymnasium_point_room(Room(4, 4), start=(1, 1))
        a, _ = env.reset(seed=99)
        x = env.step([0.1, 0.0])[0]
        b, _ = env.reset(seed=7)
        y = env.step([0.1, 0.0])[0]
        self.assertEqual(a["tick"], b["tick"])
        self.assertTrue((x["position"] == y["position"]).all())
        with self.assertRaises(ValueError):
            env.step([0.5, 0])
        env.close()


class GymnasiumAdapterDependencyTest(unittest.TestCase):
    def test_stdlib_import_is_safe(self):
        # The module import above succeeds whether or not gymnasium exists.
        self.assertTrue(callable(make_gymnasium_point_room))


if __name__ == "__main__":
    unittest.main()
