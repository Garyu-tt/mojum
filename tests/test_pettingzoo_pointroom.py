"""Deterministic stdlib gates; real external package tested if present."""
import importlib.util
import unittest

from mojum.pettingzoo_pointroom import AGENTS, PointRoomExercise
from mojum.optional_pettingzoo import make_parallel_env
from mojum.world_room import Room


class IsolatedPointRoomTests(unittest.TestCase):
    def test_replay_with_fixed_seed_and_scripted_policy(self):
        trajectories = []
        for _ in range(2):
            env = PointRoomExercise()
            frames = [env.reset(seed=13)]
            while env.agents:
                obs = frames[-1][0]
                def approach(agent):
                    own_x, own_y, peer_x, peer_y, _ = obs[agent]
                    if abs(own_x - peer_x) > 1e-10:
                        return 1 if own_x < peer_x else 2
                    return 3 if own_y < peer_y else 4
                frames.append(env.step({a: approach(a) for a in AGENTS}))
            trajectories.append(frames)
        self.assertEqual(trajectories[0], trajectories[1])
        self.assertEqual(trajectories[0][-1][1], dict.fromkeys(AGENTS, 1.0))
        self.assertTrue(all(trajectories[0][-1][2].values()))
        self.assertLessEqual(len(trajectories[0]), 10)

    def test_holdout_seeds_vs_stationary_frozen_control(self):
        for seed in (2, 5, 42, 500):
            with self.subTest(seed=seed):
                baseline = PointRoomExercise(max_cycles=8)
                baseline.reset(seed=seed)
                for _ in range(8):
                    _, rewards, ended, truncated, infos = baseline.step(dict.fromkeys(AGENTS, 0))
                    self.assertEqual(rewards, dict.fromkeys(AGENTS, 0.0))
                    self.assertFalse(any(ended.values()))
                    self.assertFalse(any(i["physical_contact"] for i in infos.values()))
                self.assertTrue(all(truncated.values()))
                scripted = PointRoomExercise(max_cycles=8)
                observation, _ = scripted.reset(seed=seed)
                for _ in range(8):
                    actions = {}
                    for agent in AGENTS:
                        x, y, peer_x, peer_y, _ = observation[agent]
                        if abs(x - peer_x) > 1e-10:
                            actions[agent] = 1 if x < peer_x else 2
                        else:
                            actions[agent] = 3 if y < peer_y else 4
                    observation, scripted_rewards, scripted_done, _, _ = scripted.step(actions)
                    if all(scripted_done.values()):
                        break
                self.assertEqual(scripted_rewards, dict.fromkeys(AGENTS, 1.0))
                self.assertTrue(all(scripted_done.values()))

    def test_obstacles_report_block_but_not_real_contact(self):
        env = PointRoomExercise(room=Room(4, 4, ((1.125, 0.0, 0.01, 4.0),)))
        env.reset(seed=2)
        # Move west/east at a standing wall if relevant to chosen seed.
        for agent in AGENTS:
            state = env._states[agent]
            if state.x < 1.125:
                actions = {a: 0 for a in AGENTS}
                actions[agent] = 1
                _, _, _, _, infos = env.step(actions)
                self.assertTrue(infos[agent]["blocked"])
                self.assertFalse(infos[agent]["physical_contact"])
                break
        else:
            self.fail("seed did not place any agent left of obstacle")

    def test_bad_action_is_atomic(self):
        env = PointRoomExercise()
        before = env.reset(seed=3)
        for bad in ({"agent_0": 1}, {"agent_0": True, "agent_1": 2},
                    {"agent_0": 88, "agent_1": 0}, [1, 2]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                env.step(bad)
            self.assertEqual(env.tick, 0)
            self.assertEqual(env._observations(), before[0])

    def test_bad_seed_and_options_do_not_modify_episode(self):
        env = PointRoomExercise()
        env.reset(seed=7)
        previous = env._observations()
        for seed in (True, -1, 1.5, "5"):
            with self.assertRaises(ValueError):
                env.reset(seed=seed)
            self.assertEqual(env._observations(), previous)
        with self.assertRaises(ValueError):
            env.reset(seed=1, options={"private_data": "no"})
        self.assertEqual(env._observations(), previous)

    def test_large_seed_replays_without_uint32_truncation(self):
        for seed in (2**32, 2**64 + 17, 2**128):
            with self.subTest(seed=seed):
                left = PointRoomExercise()
                right = PointRoomExercise()
                self.assertEqual(left.reset(seed=seed), right.reset(seed=seed))
                self.assertEqual(left.seed, seed)
                actions = dict.fromkeys(AGENTS, 0)
                self.assertEqual(left.step(actions), right.step(actions))

    def test_max_cycles_and_restart(self):
        env = PointRoomExercise(max_cycles=1)
        env.reset(seed=42)
        observations, rewards, ended, truncated, _ = env.step(dict.fromkeys(AGENTS, 0))
        self.assertEqual(set(observations), set(AGENTS))
        self.assertFalse(any(ended.values()))
        self.assertTrue(all(truncated.values()))
        with self.assertRaises(RuntimeError):
            env.step(dict.fromkeys(AGENTS, 0))
        self.assertEqual(env.reset(seed=42)[0][AGENTS[0]][-1], 0)

    def test_no_dependency_required_for_core(self):
        self.assertEqual(len(AGENTS), 2)
        self.assertEqual(PointRoomExercise().room.obstacles, ())


@unittest.skipUnless(importlib.util.find_spec("pettingzoo") is not None
                     and importlib.util.find_spec("gymnasium") is not None,
                     "optional PettingZoo and Gymnasium are not installed")
class ActualPettingZooAPITests(unittest.TestCase):
    def test_parallel_api_and_seeded_replay(self):
        from pettingzoo.test import parallel_api_test
        env = make_parallel_env()
        parallel_api_test(env, num_cycles=30)
        options_obs, options_infos = env.reset(seed=13, options={"options": 1})
        self.assertTrue(all(i["reset_options_ignored"] for i in options_infos.values()))
        first, _ = env.reset(seed=13)
        for a in AGENTS:
            self.assertTrue((options_obs[a] == first[a]).all())
        second, _ = env.reset(seed=13)
        for large_seed in (2**32, 2**64 + 17, 2**128):
            large_first, _ = env.reset(seed=large_seed)
            large_second, _ = env.reset(seed=large_seed)
            for a in AGENTS:
                self.assertTrue((large_first[a] == large_second[a]).all())
        for a in AGENTS:
            self.assertTrue((first[a] == second[a]).all())
            self.assertTrue(env.observation_space(a).contains(first[a]))
        obs, rewards, ended, truncated, infos = env.step(dict.fromkeys(AGENTS, 0))
        self.assertEqual(set(obs), set(AGENTS))
        self.assertEqual(set(rewards), set(AGENTS))
        self.assertFalse(any(ended.values()))
        self.assertFalse(any(truncated.values()))
        self.assertTrue(all(i["physical_contact"] is False for i in infos.values()))
        env.close()


if __name__ == "__main__":
    unittest.main()
