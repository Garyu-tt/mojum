"""No optional packages needed; exercise canonical point-room, not a duplicate sim."""
import sys
import unittest
from unittest import mock

from mojum.gym_room import PointRoomEpisode, make_gymnasium_env, SCHEMA
from mojum.world_room import Room


class PointRoomGymTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, .1, 2),))

    def episode(self, **options):
        defaults = {"start": (1, 2), "goal": (3, 2)}
        defaults.update(options)
        return PointRoomEpisode(self.room, **defaults)

    def test_same_seed_same_trajectory_and_revision(self):
        def run(seed):
            episode = self.episode()
            frames = [episode.reset(seed=seed)]
            for a in [1, 1, 1, 2, 0, 3]:
                frames.append(episode.step(a))
            return frames
        self.assertEqual(run(13), run(13))
        self.assertEqual(run(13)[1][4]["schema"], SCHEMA)
        self.assertFalse(run(13)[1][4]["physical_contact"])

    def test_swept_obstacle_and_no_leaked_room_geometry(self):
        episode = self.episode(step_m=1)
        observation, info = episode.reset()
        self.assertEqual(set(observation), {"position", "goal", "blocked", "tick"})
        self.assertNotIn("obstacles", str(observation))
        episode.step(1)
        obs, reward, terminated, truncated, info = episode.step(1)
        self.assertEqual(obs["position"], [1.0, 2.0])
        self.assertTrue(obs["blocked"])
        self.assertEqual(reward, -0.05)
        self.assertFalse(terminated or truncated)
        self.assertEqual(info["revision"], episode._state.revision)

    def test_wall_blocked_and_step_does_not_end(self):
        episode = self.episode(step_m=1)
        episode.reset()
        obs, reward, terminated, truncated, info = episode.step(2)
        self.assertFalse(obs["blocked"])
        obs, reward, terminated, truncated, info = episode.step(2)
        self.assertTrue(obs["blocked"])
        self.assertEqual(obs["tick"], 2)

    def test_goal_terminates_no_truncation(self):
        episode = PointRoomEpisode(Room(4, 4), start=(1, 1), goal=(1.5, 1), max_steps=2)
        episode.reset(seed=0)
        episode.step(1)
        obs, reward, terminated, truncated, _ = episode.step(1)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, 1.0)
        with self.assertRaises(RuntimeError):
            episode.step(0)

    def test_horizon_and_reset(self):
        episode = self.episode(max_steps=1)
        with self.assertRaises(RuntimeError):
            episode.step(0)
        episode.reset()
        self.assertEqual(episode.step(0)[2:4], (False, True))
        with self.assertRaises(RuntimeError):
            episode.step(0)
        self.assertEqual(episode.reset()[0]["tick"], 0)

    def test_invalid_action_does_not_advance(self):
        episode = self.episode()
        episode.reset()
        for action in (-1, 5, True, None, float("nan"), 1.0, "1", []):
            with self.subTest(action=action), self.assertRaises(ValueError):
                episode.step(action)
        self.assertEqual(episode._state.tick, 0)
        self.assertEqual(episode.step(0)[0]["tick"], 1)

    def test_mutated_episode_budgets_are_rejected_without_advancing(self):
        episode = self.episode(step_m=0.125, max_steps=2)
        episode.reset(seed=7)
        for field, invalid in (
            ("step_m", 1.0), ("step_m", float("nan")),
            ("step_m", True), ("max_steps", 20), ("max_steps", 2.0),
            ("goal_radius_m", 1.0), ("goal_radius_m", True),
        ):
            with self.subTest(field=field, invalid=invalid):
                original = getattr(episode, field)
                setattr(episode, field, invalid)
                with self.assertRaisesRegex(ValueError, "configuration mutated"):
                    episode.step(1)
                setattr(episode, field, original)
                self.assertEqual(episode._state.tick, 0)
        observation, _, _, _, _ = episode.step(1)
        self.assertEqual(observation["tick"], 1)
        self.assertEqual(observation["position"], [1.125, 2.0])

    def test_mutated_goal_start_or_room_cannot_forge_reward(self):
        # Replacing the goal with the current point must not produce a fake
        # terminal +1 reward. Restore the config and verify the state was idle.
        for field, replacement in (
            ("goal", (1.0, 2.0)),
            ("start", (3.0, 2.0)),
            ("room", Room(4, 4)),
        ):
            with self.subTest(field=field):
                episode = self.episode()
                episode.reset()
                original = getattr(episode, field)
                setattr(episode, field, replacement)
                with self.assertRaisesRegex(ValueError, "configuration mutated"):
                    episode.step(0)
                self.assertEqual(episode._state.tick, 0)
                with self.assertRaisesRegex(ValueError, "configuration mutated"):
                    episode.reset()
                setattr(episode, field, original)
                obs, reward, terminated, _, _ = episode.step(0)
                self.assertEqual(obs["tick"], 1)
                self.assertFalse(terminated)
                self.assertEqual(reward, -0.01)

    def test_bad_episode_inputs_fail_closed(self):
        for kwargs in ({"step_m": float("nan")}, {"step_m": 0},
                       {"max_steps": True}, {"goal": (2, 2)},
                       {"start": (1, float("inf"))}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.episode(**kwargs)
        episode = self.episode()
        for seed in (-1, True, "3"):
            with self.assertRaises(ValueError):
                episode.reset(seed=seed)
        with self.assertRaises(ValueError):
            episode.reset(options={"start": [3, 2]})

    def test_oversized_integer_geometry_rejected_as_value_error(self):
        # math.isfinite() raises OverflowError for integer magnitudes above
        # the platform's float range: untrusted numeric input must fail closed.
        oversized = 10**1000
        for kwargs in (
            {"start": (oversized, 1)},
            {"goal": (1, oversized)},
            {"step_m": oversized},
            {"goal_radius_m": oversized},
        ):
            with self.subTest(field=next(iter(kwargs))):
                with self.assertRaises(ValueError):
                    self.episode(**kwargs)

    def test_large_nonnegative_seed_is_accepted_and_replayed(self):
        for seed in (2**32, 2**64 + 17, 2**128):
            with self.subTest(seed=seed):
                episode = self.episode()
                first = episode.reset(seed=seed)
                self.assertEqual(first[1]["seed"], seed)
                action = episode.step(1)
                self.assertEqual(episode.reset(seed=seed), first)
                self.assertEqual(episode.step(1), action)

    def test_optional_gym_missing_fails_without_importing_on_module_load(self):
        # Guard the optional factory independently of the developer's environment.
        with mock.patch.dict(sys.modules, {"gymnasium": None}):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                make_gymnasium_env(self.room)

    def test_numpy_discrete_action_when_available(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is optional")
        episode = self.episode()
        episode.reset()
        self.assertEqual(episode.step(np.int64(1))[0]["tick"], 1)


if __name__ == "__main__":
    unittest.main()
