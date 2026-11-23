import unittest
from enum import IntEnum

from examples.pettingzoo.benchmark import DeterministicCoordinationEnv, run_benchmark


class DeterministicCoordinationEnvTests(unittest.TestCase):
    def test_reset_and_trace_are_reproducible(self):
        self.assertEqual(
            run_benchmark([0, 1, 42], horizon=8),
            run_benchmark([0, 1, 42], horizon=8),
        )

    def test_adaptive_policy_is_never_worse_than_frozen_control(self):
        result = run_benchmark(range(32), horizon=10)
        self.assertGreaterEqual(result["adaptive_reward"], result["frozen_reward"])

    def test_adaptive_policy_stays_after_first_success(self):
        result = run_benchmark(range(32), horizon=6)
        for episode in result["episodes_detail"]:
            trace = episode["adaptive_trace"]
            if trace[0]["rewards"][0] == 0:
                self.assertEqual(trace[1]["actions"], [1, 1])
                self.assertTrue(all(row["actions"] == [1, 1] for row in trace[1:]))

    def test_observations_do_not_disclose_hidden_target(self):
        env = DeterministicCoordinationEnv(horizon=2)
        observations, _ = env.reset(seed=9)
        for observation in observations.values():
            self.assertNotIn("target", observation)
            self.assertEqual(set(observation), {"agent_id", "step", "last_reward"})
            self.assertIn(observation["last_reward"], (-1, 0, 1))

    def test_parallel_step_contract_and_truncation(self):
        env = DeterministicCoordinationEnv(horizon=1)
        observations, infos = env.reset(seed=0)
        self.assertEqual(set(observations), set(env.possible_agents))
        self.assertEqual(set(infos), set(env.possible_agents))
        observations, rewards, terminations, truncations, _ = env.step(
            {"agent_0": 0, "agent_1": 0}
        )
        self.assertEqual(set(rewards), set(env.possible_agents))
        self.assertEqual(set(terminations), set(env.possible_agents))
        self.assertEqual(set(truncations), set(env.possible_agents))
        self.assertTrue(all(truncations.values()))
        self.assertFalse(any(terminations.values()))
        self.assertEqual(env.agents, [])
        self.assertEqual(set(observations), set(env.possible_agents))
        self.assertTrue(all(row["step"] == 1 for row in observations.values()))
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

    def test_rejects_hostile_or_oversized_agent_keys_before_set(self):
        class HostileKey:
            def __hash__(self):
                return 123
            def __eq__(self, other):
                raise AssertionError("hostile key equality must not execute")

        env = DeterministicCoordinationEnv()
        env.reset(seed=1)
        with self.assertRaises(ValueError):
            env.step({HostileKey(): 0, "agent_1": 0})
        with self.assertRaises(ValueError):
            env.step({"x" * 1_000_000: 0, "agent_1": 0})

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
        with self.assertRaises(ValueError):
            run_benchmark(range(1001), horizon=2)

    def test_optional_adapter_normalizes_integral_action_scalars(self):
        try:
            from examples.pettingzoo.benchmark import make_pettingzoo_env
            env = make_pettingzoo_env(horizon=1)
        except RuntimeError:
            self.skipTest("optional PettingZoo dependencies are not installed")

        class Action(IntEnum):
            ZERO = 0

        env.reset(seed=0)
        observations, rewards, terminations, truncations, infos = env.step({
            "agent_0": Action.ZERO, "agent_1": Action.ZERO,
        })
        self.assertEqual(set(rewards), set(env.possible_agents))
        self.assertTrue(all(truncations.values()))
        self.assertFalse(any(terminations.values()))
        env.close()

    def test_seeded_episode_is_bounded_and_trace_has_no_private_target(self):
        episode = run_benchmark([123], horizon=5)["episodes_detail"][0]
        self.assertEqual(len(episode["adaptive_trace"]), 5)
        self.assertEqual(len(episode["frozen_trace"]), 5)
        self.assertNotIn("target", str(episode))
        self.assertEqual(run_benchmark([123], horizon=5)["maximum_team_reward"], 10)


if __name__ == "__main__":
    unittest.main()
