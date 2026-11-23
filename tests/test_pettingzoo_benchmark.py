"""Pure-stdlib, offline synthetic PettingZoo Parallel contract tests."""
import math
import sys
import subprocess
from collections import defaultdict
import os
from pathlib import Path
import unittest
from unittest import mock

from mojum.pettingzoo_benchmark import (
    AGENTS, BenchmarkError, HELD_OUT_SEEDS, SCHEMA,
    run_benchmark, run_episode,
)


class _Discrete:
    def contains(self, action):
        return type(action) is int and 0 <= action < 3


class FakeRPS:
    """Minimal controlled parallel two-agent fixture, never an organism."""

    def __init__(self, failure=None):
        self.agents = []
        self.step_count = 0
        self.closed = False
        self.failure = failure

    def reset(self, seed=None, options=None):
        self.agents = list(AGENTS)
        self.step_count = 0
        if self.failure == "bad-reset":
            return ({}, {})
        return ({a: 3 for a in AGENTS}, {a: {} for a in AGENTS})

    def action_space(self, agent):
        return _Discrete()

    def step(self, actions):
        left, right = actions[AGENTS[0]], actions[AGENTS[1]]
        result = (left - right) % 3
        score = 0 if result == 0 else (1 if result == 1 else -1)
        self.step_count += 1
        done = self.step_count == 15
        self.agents = [] if done else list(AGENTS)
        observations = {AGENTS[0]: right, AGENTS[1]: left}
        rewards = {AGENTS[0]: score, AGENTS[1]: -score}
        terminations = {a: False for a in AGENTS}
        truncations = {a: done for a in AGENTS}
        infos = {a: {"PRIVATE": "never returned or hashed"} for a in AGENTS}
        if self.failure == "nan":
            rewards[AGENTS[0]] = math.nan
        elif self.failure == "bad-key":
            rewards["unexpected"] = 0
        elif self.failure == "bad-zero-sum":
            rewards[AGENTS[1]] = 1
        elif self.failure == "bad-done":
            truncations[AGENTS[0]] = True
        elif self.failure == "bad-observation":
            observations[AGENTS[0]] = {"private": "stuff"}
        elif self.failure == "bad-step":
            return (observations, rewards)
        elif self.failure == "bad-info":
            infos[AGENTS[0]] = "secret"
        elif self.failure == "bad-agents":
            self.agents = [AGENTS[0], AGENTS[0]]
        elif self.failure == "defaultdict-rewards":
            rewards = defaultdict(int, rewards)
        return observations, rewards, terminations, truncations, infos

    def close(self):
        self.closed = True


class PettingZooBenchmarkTests(unittest.TestCase):
    def test_explicit_gate_cannot_create_upstream(self):
        factory = mock.Mock(side_effect=AssertionError("must not construct env"))
        with self.assertRaisesRegex(BenchmarkError, "enabled=True"):
            run_episode(factory, seed=11)
        with self.assertRaisesRegex(BenchmarkError, "enabled=True"):
            run_benchmark(factory)
        factory.assert_not_called()

    def test_replay_and_better_than_frozen_heldout(self):
        one = run_benchmark(FakeRPS, enabled=True)
        two = run_benchmark(FakeRPS, enabled=True)
        self.assertEqual(one, two)
        self.assertEqual(one["schema"], SCHEMA)
        self.assertEqual(len(one["episodes"]), 2 * len(HELD_OUT_SEEDS))
        for seed in HELD_OUT_SEEDS:
            frozen, adaptive = [e for e in one["episodes"] if e["seed"] == seed]
            self.assertEqual(frozen["policy"], "frozen")
            self.assertEqual(adaptive["policy"], "online_transition")
            self.assertEqual(frozen["cycles"], 15)
            self.assertEqual(adaptive["cycles"], 15)
            self.assertGreater(adaptive["reward"], frozen["reward"])
            self.assertEqual(len(adaptive["transcript_sha256"]), 64)
        self.assertNotIn("PRIVATE", str(one))
        self.assertNotIn("player_0", str(one))

    def test_fail_closed_and_close_on_malformed_parallel_state(self):
        for failure in ("bad-reset", "nan", "bad-key", "bad-zero-sum", "bad-done",
                        "bad-observation", "bad-step", "bad-info", "bad-agents"):
            env = FakeRPS(failure)
            with self.subTest(failure=failure), self.assertRaises(BenchmarkError):
                run_episode(lambda: env, seed=11, enabled=True)
            self.assertTrue(env.closed)

    def test_parallel_defaultdict_rewards_are_supported_without_losing_key_validation(self):
        result = run_episode(lambda: FakeRPS("defaultdict-rewards"), seed=11, enabled=True)
        self.assertEqual(result["cycles"], 15)
        self.assertEqual(result["reward"], 0)

    def test_hostile_mapping_keys_fail_closed_without_running_equality(self):
        from mojum import pettingzoo_benchmark as pz

        class HostileKey(str):
            eq_calls = 0
            __hash__ = str.__hash__

            def __eq__(self, other):
                type(self).eq_calls += 1
                raise RuntimeError("hostile equality must not run")

        # A built-in dict can hold a str subclass with a colliding hash.
        # set(mapping) == set(AGENTS) calls its untrusted equality.
        for name in ("observations", "infos", "rewards", "terminations", "truncations"):
            hostile = {HostileKey(AGENTS[0]): 3, AGENTS[1]: 3}
            with self.subTest(name=name), self.assertRaises(BenchmarkError):
                pz._dict(hostile, AGENTS, name)

        class ForgedIterator(dict):
            def __iter__(self):
                return iter(AGENTS)

            def keys(self):
                return AGENTS

            def __len__(self):
                return 2

        # The real dict storage, not an overrideable iterator, must be checked.
        with self.assertRaises(BenchmarkError):
            pz._dict(ForgedIterator({"unexpected": 3, AGENTS[1]: 3}),
                     AGENTS, "observations")

        class HostileObservation(FakeRPS):
            def reset(self, seed=None, options=None):
                observations, infos = super().reset(seed=seed, options=options)
                return {HostileKey(AGENTS[0]): observations[AGENTS[0]],
                        AGENTS[1]: observations[AGENTS[1]]}, infos

        class HostileRewards(FakeRPS):
            def step(self, actions):
                obs, rewards, terminations, truncations, infos = super().step(actions)
                hostile_rewards = {HostileKey(AGENTS[0]): rewards[AGENTS[0]],
                                   AGENTS[1]: rewards[AGENTS[1]]}
                return obs, hostile_rewards, terminations, truncations, infos

        for fixture in (HostileObservation, HostileRewards):
            env = fixture()
            with self.subTest(fixture=fixture.__name__), self.assertRaises(BenchmarkError):
                run_episode(lambda: env, seed=11, enabled=True)
            self.assertTrue(env.closed)

        self.assertEqual(HostileKey.eq_calls, 0)

    def test_budget_and_argument_rejections_before_factory(self):
        factory = mock.Mock(side_effect=AssertionError("unexpected factory"))
        for params in ({"seed": True}, {"seed": -1}, {"seed": 2**32},
                       {"seed": 1, "cycles": 0}, {"seed": 1, "cycles": 65},
                       {"seed": 1, "policy": "oracle"}):
            with self.subTest(params=params), self.assertRaises(BenchmarkError):
                run_episode(factory, enabled=True, **params)
        for seeds in ((11, 11), (), (1, 2, 3, 4, 5), (True,), (2**32,)):
            with self.subTest(seeds=seeds), self.assertRaises(BenchmarkError):
                run_benchmark(factory, enabled=True, seeds=seeds)
        factory.assert_not_called()

    def test_unterminated_environment_is_rejected_and_closed(self):
        env = FakeRPS()
        with self.assertRaisesRegex(BenchmarkError, "did not terminate"):
            run_episode(lambda: env, seed=11, cycles=2, enabled=True)
        self.assertTrue(env.closed)

    def test_index_like_inputs_are_normalized_exactly_once(self):
        class ChangingIndex:
            def __init__(self, first, subsequent):
                self.first = first
                self.subsequent = subsequent
                self.calls = 0

            def __index__(self):
                self.calls += 1
                return self.first if self.calls == 1 else self.subsequent

            def __int__(self):
                # A distinct __int__ must never override the validated index.
                return 29

        seed = ChangingIndex(11, 2**50)
        cycles = ChangingIndex(15, 65)
        result = run_episode(FakeRPS, seed=seed, cycles=cycles, enabled=True)
        self.assertEqual(result["seed"], 11)
        self.assertEqual(result["cycles"], 15)
        self.assertEqual(seed.calls, 1)
        self.assertEqual(cycles.calls, 1)

        held_out = ChangingIndex(11, 2**50)
        report = run_benchmark(FakeRPS, enabled=True, seeds=[held_out])
        self.assertEqual(held_out.calls, 1)
        self.assertEqual([e["seed"] for e in report["episodes"]], [11, 11])

    def test_missing_optional_dependency_has_explicit_error(self):
        from mojum import pettingzoo_benchmark as pz
        with mock.patch.object(pz, "version", side_effect=pz.PackageNotFoundError("pettingzoo")):
            with self.assertRaisesRegex(BenchmarkError, "not installed"):
                pz._pettingzoo_factory()
        with mock.patch.object(pz, "version", return_value="0.0"):
            with self.assertRaisesRegex(BenchmarkError, "unreviewed"):
                pz._pettingzoo_factory()

    def test_actual_pinned_upstream_if_installed(self):
        from mojum import pettingzoo_benchmark as pz
        try:
            found = pz.version("pettingzoo")
        except pz.PackageNotFoundError:
            self.skipTest("optional PettingZoo not installed")
        if found != pz.UPSTREAM_VERSION:
            self.skipTest("optional PettingZoo version not pinned")
        one = pz.run_benchmark(pz._pettingzoo_factory, enabled=True)
        two = pz.run_benchmark(pz._pettingzoo_factory, enabled=True)
        self.assertEqual(one, two)
        for seed in HELD_OUT_SEEDS:
            frozen, online = [e for e in one["episodes"] if e["seed"] == seed]
            self.assertGreater(online["reward"], frozen["reward"])

    def test_import_has_no_pettingzoo_side_effect(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.modules['pettingzoo']=None; "
             "import mojum.pettingzoo_benchmark"], env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
