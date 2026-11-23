"""Deterministic, stdlib-only visual-navigation fixture inspired by ViZDoom.

This is a synthetic grid benchmark, not a ViZDoom engine binding. The optional
ViZDoom package is never imported unless require_vizdoom() is called explicitly.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import importlib
import json
import random
from typing import Any

ACTIONS = ("UP", "RIGHT", "DOWN", "LEFT")
_DELTAS = {"UP": (-1, 0), "RIGHT": (0, 1), "DOWN": (1, 0), "LEFT": (0, -1)}
_SIZE = 7
_START = (0, 0)
_GOAL = (_SIZE - 1, _SIZE - 1)


@dataclass(frozen=True)
class StepResult:
    observation: tuple[str, ...]
    reward: int
    terminated: bool
    truncated: bool
    info: dict[str, Any]


class OfflineNavigationEnv:
    """Small bounded visual maze with reproducible seeded obstacles."""

    def __init__(self, *, max_steps: int = 32, wall_probability: float = 0.18):
        if type(max_steps) is not int or not 1 <= max_steps <= 256:
            raise ValueError("max_steps must be an integer in [1, 256]")
        if type(wall_probability) not in (int, float) or isinstance(wall_probability, bool):
            raise ValueError("wall_probability must be a finite number")
        if not 0.0 <= float(wall_probability) <= 0.35:
            raise ValueError("wall_probability must be in [0, 0.35]")
        self.max_steps = max_steps
        self.wall_probability = float(wall_probability)
        self.seed = 0
        self.position = _START
        self.steps = 0
        self.done = False
        self.walls: frozenset[tuple[int, int]] = frozenset()
        self.reset(seed=0)

    def reset(self, *, seed: int = 0) -> tuple[str, ...]:
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be an integer in [0, 2**32 - 1]")
        self.seed = seed
        rng = random.Random(seed)
        # Keep the top row and rightmost column clear: a route always exists.
        self.walls = frozenset(
            (r, c)
            for r in range(1, _SIZE)
            for c in range(_SIZE - 1)
            if rng.random() < self.wall_probability
        )
        self.position = _START
        self.steps = 0
        self.done = False
        return self.observe()

    def observe(self) -> tuple[str, ...]:
        rows = []
        for r in range(_SIZE):
            row = []
            for c in range(_SIZE):
                point = (r, c)
                row.append("@" if point == self.position else "G" if point == _GOAL else "#" if point in self.walls else ".")
            rows.append("".join(row))
        return tuple(rows)

    def step(self, action: str) -> StepResult:
        if self.done:
            raise RuntimeError("episode is finished; call reset() before step()")
        if type(action) is not str or action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        dr, dc = _DELTAS[action]
        candidate = (self.position[0] + dr, self.position[1] + dc)
        blocked = (
            not (0 <= candidate[0] < _SIZE and 0 <= candidate[1] < _SIZE)
            or candidate in self.walls
        )
        if not blocked:
            self.position = candidate
        self.steps += 1
        terminated = self.position == _GOAL
        truncated = self.steps >= self.max_steps and not terminated
        self.done = terminated or truncated
        reward = 10 if terminated else -1
        return StepResult(
            observation=self.observe(),
            reward=reward,
            terminated=terminated,
            truncated=truncated,
            info={"seed": self.seed, "step": self.steps, "blocked": blocked, "position": self.position},
        )


def shortest_path(env: OfflineNavigationEnv) -> tuple[str, ...]:
    """Deterministic BFS baseline; fixed ACTIONS ordering breaks ties."""
    queue = deque([(_START, ())])
    seen = {_START}
    while queue:
        point, path = queue.popleft()
        if point == _GOAL:
            return path
        for action in ACTIONS:
            dr, dc = _DELTAS[action]
            nxt = (point[0] + dr, point[1] + dc)
            if 0 <= nxt[0] < _SIZE and 0 <= nxt[1] < _SIZE and nxt not in env.walls and nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + (action,)))
    raise RuntimeError("generated maze has no path to goal")


def run_episode(*, seed: int = 0, max_steps: int = 32, policy: str = "frozen") -> dict[str, Any]:
    if policy not in ("frozen", "wait"):
        raise ValueError("policy must be 'frozen' or 'wait'")
    env = OfflineNavigationEnv(max_steps=max_steps)
    initial = env.reset(seed=seed)
    actions = shortest_path(env) if policy == "frozen" else ("WAIT",) * max_steps
    trace = []
    total_reward = 0
    for action in actions[:max_steps]:
        result = env.step("UP" if action == "WAIT" else action)
        total_reward += result.reward
        trace.append({
            "action": action,
            "reward": result.reward,
            "terminated": result.terminated,
            "truncated": result.truncated,
            "position": list(result.info["position"]),
            "blocked": result.info["blocked"],
        })
        if result.terminated or result.truncated:
            break
    return {
        "schema": "mojum.vizdoom-offline-navigation/1",
        "source": "PUBLIC_SYNTHETIC",
        "engine": "stdlib-grid-fixture",
        "seed": seed,
        "policy": policy,
        "initial_observation": list(initial),
        "trace": trace,
        "steps": len(trace),
        "total_reward": total_reward,
        "success": bool(trace and trace[-1]["terminated"]),
        "truncated": bool(trace and trace[-1]["truncated"]),
    }


def require_vizdoom() -> Any:
    """Explicitly import an already-installed optional ViZDoom package."""
    try:
        return importlib.import_module("vizdoom")
    except ImportError as exc:
        raise RuntimeError(
            "Optional ViZDoom is not installed. Install it separately; "
            "the offline fixture does not require it."
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--policy", choices=("frozen", "wait"), default="frozen")
    args = parser.parse_args()
    print(json.dumps(run_episode(seed=args.seed, max_steps=args.max_steps, policy=args.policy), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
