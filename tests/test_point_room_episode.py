import unittest

from mojum.point_room_episode import PointRoomEpisode, SCHEMA
from mojum.world_room import Room


class PointRoomEpisodeTests(unittest.TestCase):
    def test_deterministic_seed_and_actions(self):
        def run():
            env = PointRoomEpisode(Room(4, 4), max_steps=3)
            result = [env.reset(seed=17)]
            for _ in range(3):
                result.append(env.step((0, 0)))
            return result
        self.assertEqual(run(), run())
        self.assertEqual(run()[-1][3], True)

    def test_goal_terminates_and_precedes_truncation(self):
        env = PointRoomEpisode(Room(4, 4), max_steps=1)
        env.reset(start=(1, 1), goal=(1.2, 1))
        obs, reward, terminated, truncated, info = env.step((.2, 0))
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, 1.0)
        self.assertEqual(info["schema"], SCHEMA)
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_blocked_move_consumes_tick(self):
        env = PointRoomEpisode(Room(4, 4, ((1.1, 1, .01, 1),)),
                               max_steps=1)
        env.reset(start=(1, 1.5), goal=(3, 3))
        obs, reward, terminated, truncated, info = env.step((.2, 0))
        self.assertTrue(info["blocked"])
        self.assertEqual(obs["position"], [1, 1.5])
        self.assertEqual(obs["tick"], 1)
        self.assertEqual(reward, -.01)
        self.assertFalse(terminated)
        self.assertTrue(truncated)

    def test_invalid_action_does_not_advance(self):
        env = PointRoomEpisode(Room(4, 4))
        env.reset(start=(1, 1), goal=(3, 3))
        for action in ((1, 0), (float("nan"), 0), (True, 0), "bad"):
            with self.assertRaises(ValueError):
                env.step(action)
        self.assertEqual(env.step((0, 0))[0]["tick"], 1)

    def test_invalid_reset_does_not_start_episode(self):
        env = PointRoomEpisode(Room(4, 4))
        with self.assertRaises(ValueError):
            env.reset(start=(5, 5), goal=(1, 1))
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_invalid_configuration(self):
        for kwargs in ({"max_steps": True}, {"max_steps": 0},
                       {"max_action_m": float("inf")},
                       {"goal_radius_m": 0}):
            with self.assertRaises(ValueError):
                PointRoomEpisode(Room(4, 4), **kwargs)

    def test_observation_is_detached(self):
        env = PointRoomEpisode(Room(4, 4))
        obs, _ = env.reset(start=(1, 1), goal=(3, 3))
        obs["position"][0] = 999
        obs["goal"][0] = 999
        nxt = env.step((0, 0))[0]
        self.assertEqual(nxt["position"], [1, 1])
        self.assertEqual(nxt["goal"], [3, 3])


if __name__ == "__main__":
    unittest.main()
