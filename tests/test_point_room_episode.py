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

    def test_constructor_bounds_cannot_be_changed_during_an_episode(self):
        for field, replacement, action in (
            ("max_steps", 100, (0, 0)),
            ("max_action_m", 1.0, (1, 0)),
            ("goal_radius_m", 4.0, (0, 0)),
            ("room", Room(4, 4), (0, 0)),
        ):
            with self.subTest(field=field):
                env = PointRoomEpisode(Room(4, 4), max_steps=1,
                                       max_action_m=.25, goal_radius_m=.1)
                env.reset(start=(1, 1), goal=(3, 3))
                before = env._state
                original = getattr(env, field)
                setattr(env, field, replacement)
                with self.assertRaisesRegex(ValueError, "configuration mutated"):
                    env.step(action)
                self.assertIs(env._state, before)
                self.assertEqual(env._goal, (3, 3))
                self.assertFalse(env._done)
                setattr(env, field, original)
                obs, reward, terminated, truncated, _ = env.step((0, 0))
                self.assertEqual(obs["tick"], 1)
                self.assertEqual(obs["position"], [1, 1])
                self.assertEqual((reward, terminated, truncated), (0.0, False, True))

    def test_failed_reset_preserves_episode_when_configuration_was_mutated(self):
        for finished in (False, True):
            for field, replacement in (
                ("max_steps", 100), ("max_action_m", float("nan")),
                ("goal_radius_m", .2), ("room", Room(5, 5)),
            ):
                with self.subTest(finished=finished, field=field):
                    env = PointRoomEpisode(Room(4, 4), max_steps=1 if finished else 2)
                    env.reset(start=(1, 1), goal=(3, 3))
                    env.step((.1, 0))
                    before, goal = env._state, env._goal
                    original = getattr(env, field)
                    setattr(env, field, replacement)
                    with self.assertRaisesRegex(ValueError, "configuration mutated"):
                        env.reset(seed=17, start=(2, 2), goal=(3, 3))
                    self.assertIs(env._state, before)
                    self.assertIs(env._goal, goal)
                    self.assertEqual(env._done, finished)
                    setattr(env, field, original)
                    if finished:
                        with self.assertRaisesRegex(RuntimeError, "episode finished"):
                            env.step((0, 0))
                    else:
                        self.assertTrue(env.step((0, 0))[3])

    def test_configuration_aliases_are_rejected_without_comparison_hooks(self):
        class IntAlias(int):
            def __eq__(self, other):
                raise AssertionError("integer equality hook called")

            def __ne__(self, other):
                raise AssertionError("integer inequality hook called")

        class FloatAlias(float):
            def __eq__(self, other):
                raise AssertionError("float equality hook called")

            def __ne__(self, other):
                raise AssertionError("float inequality hook called")

        for field, replacement in (
            ("max_steps", True), ("max_steps", 1.0), ("max_steps", IntAlias(1)),
            ("max_action_m", FloatAlias(.25)), ("goal_radius_m", FloatAlias(.1)),
        ):
            with self.subTest(field=field, replacement_type=type(replacement).__name__):
                env = PointRoomEpisode(Room(4, 4), max_steps=1)
                env.reset(start=(1, 1), goal=(3, 3))
                before = env._state
                setattr(env, field, replacement)
                with self.assertRaisesRegex(ValueError, "configuration mutated"):
                    env.step((0, 0))
                self.assertIs(env._state, before)

    def test_reset_can_choose_new_seed_start_and_goal_with_fixed_configuration(self):
        env = PointRoomEpisode(Room(4, 4), max_steps=1)
        env.reset(seed=7, start=(1, 1), goal=(3, 3))
        self.assertTrue(env.step((0, 0))[3])
        obs, info = env.reset(seed=19, start=(2, 2), goal=(2.2, 2))
        self.assertEqual(obs, {"position": [2, 2], "goal": [2.2, 2], "tick": 0})
        self.assertEqual(info["seed"], 19)
        _, reward, terminated, truncated, _ = env.step((.2, 0))
        self.assertEqual((reward, terminated, truncated), (1.0, True, False))

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
