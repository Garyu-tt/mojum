import unittest

from mojum.point_room_env import PointRoomEnv, SCHEMA
from mojum.world_room import Room


class PointRoomEnvTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(5, 5, ((2, 0, 1, 4),))

    def test_reset_and_step_use_canonical_logical_ticks(self):
        env = PointRoomEnv(self.room, start_position=(1, 1), max_episode_steps=3)
        obs, info = env.reset(seed=7)
        self.assertEqual(obs["schema"], SCHEMA)
        self.assertEqual(obs["tick"], 0)
        self.assertEqual(info["provenance"], "PUBLIC_SYNTHETIC")
        next_obs, reward, terminated, truncated, step_info = env.step((0.25, 0))
        self.assertEqual(next_obs["tick"], 1)
        self.assertEqual((next_obs["x"], next_obs["y"]), (1.25, 1))
        self.assertEqual(reward, 0.0)
        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertEqual(step_info["revision"], env.state.revision)

    def test_wall_collision_is_reported_without_fake_physical_contact(self):
        env = PointRoomEnv(self.room, start_position=(1, 1), max_episode_steps=2, max_action_m=3)
        env.reset(seed=1)
        obs, _, terminated, truncated, info = env.step((2, 0))
        self.assertEqual((obs["x"], obs["y"]), (1, 1))
        self.assertTrue(info["blocked"])
        self.assertFalse(info["physical_contact"])
        self.assertFalse(terminated)
        self.assertFalse(truncated)

    def test_seed_reproduces_spawn_selection(self):
        env = PointRoomEnv(self.room, start_position=(1, 1), spawn_positions=((1, 1), (1, 4), (4, 4)))
        first, _ = env.reset(seed=1234)
        second, _ = env.reset(seed=1234)
        self.assertEqual((first["x"], first["y"]), (second["x"], second["y"]))

    def test_goal_terminates_and_returns_reward(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1), goal_position=(2, 1), goal_tolerance_m=0.01)
        env.reset(seed=0)
        obs, reward, terminated, truncated, _ = env.step((1, 0))
        self.assertEqual((obs["x"], obs["y"]), (2, 1))
        self.assertEqual(reward, 1.0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_step_budget_truncates_without_goal(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1), max_episode_steps=1)
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step((0, 0))
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_goal_takes_precedence_over_final_step_truncation(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1), goal_position=(1, 1), max_episode_steps=1)
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step((0, 0))
        self.assertTrue(terminated)
        self.assertFalse(truncated)

    def test_reset_option_is_validated(self):
        env = PointRoomEnv(self.room, start_position=(1, 1))
        with self.assertRaises(ValueError):
            env.reset(seed=0, options={"teleport": (2, 2)})
        with self.assertRaises(ValueError):
            env.reset(options={"start_position": (True, 1)})
        with self.assertRaises(ValueError):
            env.reset(options={"start_position": (2.5, 2)})

    def test_observation_and_info_are_detached(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1))
        obs, info = env.reset(seed=0)
        obs["x"] = 999
        info["seed"] = 999
        self.assertEqual(env.state.x, 1)
        _, _, _, _, step_info = env.step((0, 0))
        step_info["blocked"] = True
        self.assertFalse(env._last_info["blocked"])

    def test_action_rejects_bool_nonfinite_and_wrong_shapes(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1))
        env.reset(seed=0)
        for action in ((True, 0), (float("nan"), 0), (float("inf"), 0), (1,), {"dx": 1, "dy": 0}):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    env.step(action)

    def test_action_budget_rejects_large_displacement(self):
        env = PointRoomEnv(Room(100, 100), start_position=(1, 1), max_action_m=0.5)
        env.reset(seed=0)
        with self.assertRaises(ValueError):
            env.step((0.5, 0.5))
        self.assertEqual(env.state.tick, 0)

    def test_requires_reset_before_first_step_and_after_completion(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1), max_episode_steps=1)
        with self.assertRaises(RuntimeError):
            env.step((0, 0))
        env.reset(seed=0)
        env.step((0, 0))
        with self.assertRaises(RuntimeError):
            env.step((0, 0))

    def test_bad_constructor_values_fail_closed(self):
        with self.assertRaises(ValueError):
            PointRoomEnv(self.room, start_position=(1, 1), max_episode_steps=True)
        with self.assertRaises(ValueError):
            PointRoomEnv(self.room, start_position=(1, 1), max_action_m=float("inf"))
        with self.assertRaises(ValueError):
            PointRoomEnv(self.room, start_position=(1, 1), spawn_positions=((1, 1), (1, 1)))
        with self.assertRaises(ValueError):
            PointRoomEnv(self.room, start_position=(1, 1), spawn_positions=((1, 1), (2.5, 2)))

    def test_seed_rejects_bool_and_out_of_range_values(self):
        env = PointRoomEnv(self.room, start_position=(1, 1))
        for seed in (True, -1, 1 << 63, 1.0):
            with self.subTest(seed=seed):
                with self.assertRaises(ValueError):
                    env.reset(seed=seed)

    def test_reset_with_none_seed_reuses_rng_stream(self):
        env = PointRoomEnv(Room(5, 5), start_position=(1, 1), spawn_positions=((1, 1), (2, 2), (3, 3)))
        other = PointRoomEnv(Room(5, 5), start_position=(1, 1), spawn_positions=((1, 1), (2, 2), (3, 3)))
        env.reset(seed=42)
        other.reset(seed=42)
        first = [env.reset()[0] for _ in range(8)]
        second = [other.reset()[0] for _ in range(8)]
        self.assertEqual([(x["x"], x["y"]) for x in first], [(x["x"], x["y"]) for x in second])

    def test_exact_native_room_instance_required(self):
        class RoomSubclass(Room):
            pass
        with self.assertRaises(ValueError):
            PointRoomEnv(RoomSubclass(5, 5), start_position=(1, 1))


if __name__ == "__main__":
    unittest.main()
