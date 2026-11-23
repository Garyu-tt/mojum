"""Deterministic, CPU-only multi-agent benchmark with an optional PettingZoo adapter."""
from __future__ import annotations

import argparse
import json
import random
from itertools import islice
from typing import Iterable


class DeterministicCoordinationEnv:
    """Two agents coordinate on a hidden, episode-constant binary target."""

    metadata = {"name": "mojum_deterministic_coordination_v1", "render_modes": []}
    possible_agents = ("agent_0", "agent_1")

    def __init__(self, horizon: int = 10) -> None:
        if type(horizon) is not int or not 1 <= horizon <= 256:
            raise ValueError("horizon must be an integer in [1, 256]")
        self.horizon = horizon
        self.agents: list[str] = []
        self._target = 0
        self._step_count = 0
        self._last_rewards: dict[str, int] = {}
        self._done = True

    def reset(self, seed: int = 0, options: dict | None = None):
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be an integer in [0, 2**32 - 1]")
        if options is not None and type(options) is not dict:
            raise ValueError("options must be a dict or None")
        self._target = random.Random(seed).randrange(2)
        self._step_count = 0
        self._last_rewards = {agent: -1 for agent in self.possible_agents}
        self.agents = list(self.possible_agents)
        self._done = False
        return self._observations(), {agent: {"step": 0} for agent in self.agents}

    def _observations(self):
        return {
            agent: {
                "agent_id": index,
                "step": self._step_count,
                "last_reward": self._last_rewards[agent],
            }
            for index, agent in enumerate(self.possible_agents)
            if agent in self.agents
        }

    def step(self, actions: dict[str, int]):
        if self._done:
            raise RuntimeError("step() called outside an active episode; call reset()")
        if type(actions) is not dict or len(actions) != len(self.agents):
            raise ValueError("actions must contain exactly one action for each active agent")
        # Bound cardinality and reject hostile/oversized keys before set hashing.
        for agent in actions:
            if type(agent) is not str or len(agent) > 16:
                raise ValueError("action keys must be bounded native agent identifiers")
        if set(actions) != set(self.agents):
            raise ValueError("actions must contain exactly one action for each active agent")
        if any(type(action) is not int or action not in (0, 1) for action in actions.values()):
            raise ValueError("each action must be the integer 0 or 1 (bool is not accepted)")

        coordinated = len(set(actions.values())) == 1
        reward = int(coordinated and next(iter(actions.values())) == self._target)
        self._step_count += 1
        rewards = {agent: reward for agent in self.agents}
        self._last_rewards = rewards.copy()
        terminations = {agent: False for agent in self.agents}
        is_truncated = self._step_count >= self.horizon
        truncations = {agent: is_truncated for agent in self.agents}
        infos = {agent: {"step": self._step_count} for agent in self.agents}
        # ParallelEnv returns one final observation for every agent that
        # acted in this step, even when the step truncates the episode.
        observations = self._observations()
        if is_truncated:
            self.agents = []
            self._done = True
        return observations, rewards, terminations, truncations, infos

    def state(self) -> tuple[int, int]:
        """Return centralized public state without revealing the hidden target."""
        return (self._step_count, self.horizon)

    def close(self) -> None:
        self.agents = []
        self._done = True


def make_pettingzoo_env(horizon: int = 10):
    """Build a real PettingZoo ParallelEnv, importing optional packages lazily."""
    try:
        from gymnasium import spaces
        from pettingzoo.utils.env import ParallelEnv
    except ImportError as exc:
        raise RuntimeError(
            "Optional adapter dependencies are missing. Install the pinned "
            "versions from docs/PETTINGZOO_EXAMPLE_R01.md; offline mode needs neither."
        ) from exc

    class MojumParallelEnv(ParallelEnv):
        metadata = DeterministicCoordinationEnv.metadata

        def __init__(self):
            self._core = DeterministicCoordinationEnv(horizon=horizon)
            self.possible_agents = list(self._core.possible_agents)
            self.agents = []
            self.render_mode = None
            self.action_spaces = {
                agent: spaces.Discrete(2) for agent in self.possible_agents
            }
            self.observation_spaces = {
                agent: spaces.Dict({
                    "agent_id": spaces.Discrete(2),
                    "step": spaces.Discrete(horizon + 1),
                    "last_reward": spaces.Discrete(3, start=-1),
                })
                for agent in self.possible_agents
            }

        def observation_space(self, agent):
            return self.observation_spaces[agent]

        def action_space(self, agent):
            return self.action_spaces[agent]

        def reset(self, seed=None, options=None):
            observations, infos = self._core.reset(
                seed=0 if seed is None else seed, options=options
            )
            self.agents = list(self._core.agents)
            return observations, infos

        def step(self, actions):
            result = self._core.step(actions)
            self.agents = list(self._core.agents)
            return result

        def state(self):
            return self._core.state()

        def render(self):
            return None

        def close(self):
            self._core.close()
            self.agents = []

    return MojumParallelEnv()


def _run_episode(seed: int, horizon: int, adaptive: bool) -> tuple[int, list[dict]]:
    env = DeterministicCoordinationEnv(horizon=horizon)
    env.reset(seed=seed)
    last_action: int | None = None
    last_reward: int | None = None
    total_reward = 0
    trace: list[dict] = []

    while env.agents:
        if not adaptive or last_action is None:
            action = 0
        elif last_reward == 1:
            action = last_action
        else:
            action = 1 - last_action
        actions = {agent: action for agent in env.agents}
        _, rewards, _, truncations, _ = env.step(actions)
        total_reward += sum(rewards.values())
        trace.append({
            "step": len(trace),
            "actions": [actions[agent] for agent in sorted(actions)],
            "rewards": [rewards[agent] for agent in sorted(rewards)],
            "truncated": all(truncations.values()),
        })
        last_action = action
        last_reward = rewards["agent_0"]

    env.close()
    return total_reward, trace


def run_benchmark(seeds: Iterable[int], horizon: int = 10) -> dict:
    """Compare adaptive win-stay/lose-switch with a frozen action-0 control."""
    if type(horizon) is not int or not 1 <= horizon <= 256:
        raise ValueError("horizon must be an integer in [1, 256]")
    seed_list = list(islice(seeds, 1001))
    if not 1 <= len(seed_list) <= 1000:
        raise ValueError("provide between 1 and 1000 episode seeds")
    if any(type(seed) is not int or not 0 <= seed <= 2**32 - 1 for seed in seed_list):
        raise ValueError("all seeds must be integers in [0, 2**32 - 1]")
    if len(set(seed_list)) != len(seed_list):
        raise ValueError("episode seeds must be unique")

    adaptive_total = 0
    frozen_total = 0
    episodes = []
    for seed in seed_list:
        adaptive_score, adaptive_trace = _run_episode(seed, horizon, adaptive=True)
        frozen_score, frozen_trace = _run_episode(seed, horizon, adaptive=False)
        adaptive_total += adaptive_score
        frozen_total += frozen_score
        episodes.append({
            "seed": seed,
            "adaptive_reward": adaptive_score,
            "frozen_reward": frozen_score,
            "adaptive_trace": adaptive_trace,
            "frozen_trace": frozen_trace,
        })

    maximum = 2 * horizon * len(seed_list)
    return {
        "schema": "mojum.pettingzoo-benchmark/1",
        "episodes": len(seed_list),
        "horizon": horizon,
        "maximum_team_reward": maximum,
        "adaptive_reward": adaptive_total,
        "frozen_reward": frozen_total,
        "adaptive_rate": adaptive_total / maximum,
        "frozen_rate": frozen_total / maximum,
        "episodes_detail": episodes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=10)
    args = parser.parse_args(argv)
    if not 0 <= args.seed <= 2**32 - 1 or not 1 <= args.episodes <= 1000:
        parser.error("seed or episode count is outside its supported range")
    if args.seed + args.episodes - 1 > 2**32 - 1:
        parser.error("episode seed range exceeds 2**32 - 1")
    result = run_benchmark(range(args.seed, args.seed + args.episodes), args.horizon)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
