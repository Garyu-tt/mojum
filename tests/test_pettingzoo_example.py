import unittest

from examples.pettingzoo.benchmark import DeterministicCoordinationEnv, run_benchmark


class DeterministicCoordinationEnvTests(unittest.TestCase):
    def test_reset_and_trace_are_reproducible(self):
        self.assertEqual(
            run_benchmark([0, 1, 42], horizon=8),
            run_benchmark([0, 1, 42], horizon=8),
        )

    def test_adaptive_policy_beats_frozen_control_on_seed_set(self):
        result = run_benchmark(range(32), horizon=10)
        self.assertGreater(result["adaptive_reward"], result["frozen_reward"])

    def test_observations_do_not_disclose_hidden_target(self):
        env = DeterministicCoordinationEnv(horizon=2)
        observations, _ = env.reset(seed=9)
        for observation in observations.values():
            self.assertNotIn("target", observation)
            self.assertEqual(set(observation), {"agent_id", "step", "last_reward"})

    def test_parallel_step_contract_and_truncation(self):
        env = DeterministicCoordinationEnv(horizon=1)
        observations, infos = env.reset(seed=0)
        self.assertEqual(set(observations), set(env.possible_agents))
        self.assertEqual(set(infos), set(env.possible_agents))
        _, rewards, terminations, truncations, _ = env.step(
            {"agent_0": 0, "agent_1": 0}
        )
        self.assertEqual(set(rewards), set(env.possible_agents))
        self.assertEqual(set(terminations), set(env.possible_agents))
        self.assertEqual(set(truncations), set(env.possible_agents))
        self.assertTrue(all(truncations.values()))
        self.assertFalse(any(terminations.values()))
        self.assertEqual(env.agents, [])
        with self.assertRaises(RuntimeError):
            env.step({"agent_0": 0, "agent_1": 0})

    def test_rejects_incomplete_or_hostile_actions(self):
        env = DeterministicCoordinationEnv()
        env.reset(seed=1)
        with self.assertRaises(ValueError):
            env.step({"agent_0": 0})
        with self.assertRaises(ValueError):
            env.step({"agent_0": True, "agent_1": 0})
        with self.assertRaises(ValueError):
            env.step({"agent_0": 0, "agent_1": 2})

    def test_rejects_bool_seed_and_invalid_horizon(self):
        with self.assertRaises(ValueError):
            DeterministicCoordinationEnv(horizon=True)
        with self.assertRaises(ValueError):
            DeterministicCoordinationEnv(horizon=257)
        with self.assertRaises(ValueError):
            DeterministicCoordinationEnv().reset(seed=True)

    def test_benchmark_bounds_and_unique_seed_validation(self):
        with self.assertRaises(ValueError):
            run_benchmark([], horizon=2)
        with self.assertRaises(ValueError):
            run_benchmark([1, 1], horizon=2)
        with self.assertRaises(ValueError):
            run_benchmark([False], horizon=2)

    def test_seeded_episode_is_bounded_and_trace_has_no_private_target(self):
        episode = run_benchmark([123], horizon=5)["episodes_detail"][0]
        self.assertEqual(len(episode["adaptive_trace"]), 5)
        self.assertEqual(len(episode["frozen_trace"]), 5)
        self.assertNotIn("target", str(episode))
        self.assertEqual(run_benchmark([123], horizon=5)["maximum_team_reward"], 10)


if __name__ == "__main__":
    unittest.main()
