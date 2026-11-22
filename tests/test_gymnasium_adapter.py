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
