"""Opt-in PUBLIC_SYNTHETIC point-room SB3 benchmark (issue #754).

No dependency or network use on import. No access to canonical journals,
Noosphere, runtime motor policy, or real actuators. Requires the proposed
Gymnasium bridge in PR #725; keep the benchmark stacked until that is accepted.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import operator
import random
import sys
import time
from dataclasses import dataclass

from .world_room import Room

SCHEMA = "mojum.sb3.synthetic-benchmark/1"
SB3_PIN = "2.9.2"
GYMNASIUM_PIN = "1.3.0"
TRAIN_SEEDS = (11, 19, 23, 29)
HELDOUT_SEEDS = (101, 103, 107, 109)
MAX_STEPS = 64
ACTION_COUNT = 5
MAX_TIMESTEPS = 32768


@dataclass(frozen=True)
class Scenario:
    seed: int
    room: Room
    start: tuple[float, float]
    goal: tuple[float, float]


def _seed(value: int) -> int:
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError("invalid public synthetic seed")
    return value


def scenario(seed: int) -> Scenario:
    """Create a public 4 m x 4 m point-room from one validated seed.

    Every scenario uses identical observation/action spaces. These fixed seed
    suites are distinct scene layouts, not claims of real-world transfer.
    """
    rng = random.Random(_seed(seed))
    # Chosen coordinates are interior and align with exact 0.25m actions.
    grid = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
    start = (rng.choice(grid), rng.choice(grid))
    goal = (rng.choice(grid), rng.choice(grid))
    while math.dist(start, goal) < 1.5:
        goal = (rng.choice(grid), rng.choice(grid))
    return Scenario(seed, Room(4.0, 4.0), start, goal)


def _validate_splits(train_seeds=TRAIN_SEEDS, heldout_seeds=HELDOUT_SEEDS):
    for name, seeds in (("train", train_seeds), ("heldout", heldout_seeds)):
        if type(seeds) is not tuple or not 1 <= len(seeds) <= 16:
            raise ValueError(f"invalid {name} seed set")
        for seed in seeds:
            _seed(seed)
        if len(set(seeds)) != len(seeds):
            raise ValueError(f"duplicate {name} seed")
    if set(train_seeds) & set(heldout_seeds):
        raise ValueError("train/heldout seed overlap")
    train_scenes = {(scenario(s).start, scenario(s).goal) for s in train_seeds}
    heldout_scenes = {(scenario(s).start, scenario(s).goal) for s in heldout_seeds}
    if train_scenes & heldout_scenes:
        raise ValueError("train/heldout scene overlap")


def _score_policy(policy_factory, seeds=HELDOUT_SEEDS):
    """Passive evaluation against the optional stdlib Gymnasium episode.

    policy_factory(seed) returns a callable receiving *only the observation*.
    It never receives the Room, authoritative RoomState, or future outcomes.
    """
    from .gym_room import PointRoomEpisode

    if type(seeds) is not tuple or not 1 <= len(seeds) <= 16:
        raise ValueError("invalid evaluation seeds")
    if len(set(seeds)) != len(seeds):
        raise ValueError("duplicate evaluation seed")
    records = []
    for seed in seeds:
        item = scenario(seed)
        episode = PointRoomEpisode(item.room, start=item.start, goal=item.goal,
                                   step_m=0.25, goal_radius_m=0.125, max_steps=MAX_STEPS)
        obs, _ = episode.reset(seed=seed)
        policy = policy_factory(seed)
        if not callable(policy):
            raise ValueError("policy must be callable")
        total = 0.0
        blocked = 0
        for step in range(1, MAX_STEPS + 1):
            raw_action = policy(obs)
            if type(raw_action) is bool:
                raise ValueError("invalid policy action")
            try:
                action = operator.index(raw_action)
            except TypeError as exc:
                raise ValueError("invalid policy action") from exc
            if not 0 <= action < ACTION_COUNT:
                raise ValueError("invalid policy action")
            obs, reward, terminated, truncated, info = episode.step(action)
            if type(reward) not in (float, int) or not math.isfinite(reward):
                raise ValueError("nonfinite reward")
            total += reward
            blocked += int(info["blocked"])
            if terminated or truncated:
                records.append({"seed": seed, "steps": step, "return": round(total, 9),
                                "reached_goal": bool(terminated), "blocked": blocked})
                break
        else:
            raise AssertionError("episode exceeded bounded horizon")
    return {"episodes": records, "success_rate": sum(r["reached_goal"] for r in records) / len(records),
            "mean_return": round(sum(r["return"] for r in records) / len(records), 9),
            "blocked_total": sum(r["blocked"] for r in records)}


def evaluate_frozen(seeds=HELDOUT_SEEDS):
    """Frozen, uniformly random action control (no training, no updates)."""
    return _score_policy(
        lambda seed: lambda _obs, rng=random.Random(seed + 50000): rng.randrange(ACTION_COUNT), seeds)


def _require_optional_versions():
    for name, required in (("stable-baselines3", SB3_PIN), ("gymnasium", GYMNASIUM_PIN)):
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"optional {name}=={required} not installed") from exc
        if installed != required:
            raise RuntimeError(f"expected {name}=={required}, got {installed}")


def run_native(*, timesteps=2048, policy_seed=2026):
    """Explicit CPU-only PPO training; no model files or network I/O.

    The true upstream deps, Python/torch/platform pins and physical budgets
    require separate native CI verification; no 'improvement' is assumed.
    """
    _validate_splits()
    if type(timesteps) is not int or not 128 <= timesteps <= MAX_TIMESTEPS or timesteps % 128:
        raise ValueError("timesteps must be a multiple of 128 in [128, 32768]")
    _seed(policy_seed)
    _require_optional_versions()
    try:
        import gymnasium as gym
        from stable_baselines3 import PPO
        import torch
        from .gym_room import make_gymnasium_env
    except ImportError as exc:
        raise RuntimeError("optional SB3 + Gymnasium + PyTorch + #725 adapter required") from exc

    # Only synthetic scenarios are accepted; no externally supplied manifests.
    class CyclingPublicScenes(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self._counter = 0
            self._active = None
            prototype = scenario(TRAIN_SEEDS[0])
            self._prototype = make_gymnasium_env(prototype.room, start=prototype.start,
                                                 goal=prototype.goal, max_steps=MAX_STEPS)
            self.action_space = self._prototype.action_space
            self.observation_space = self._prototype.observation_space

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if self._active is not None:
                self._active.close()
            s = scenario(TRAIN_SEEDS[self._counter % len(TRAIN_SEEDS)])
            self._counter += 1
            self._active = make_gymnasium_env(s.room, start=s.start, goal=s.goal,
                                              max_steps=MAX_STEPS)
            return self._active.reset(seed=seed, options=options)

        def step(self, action):
            if self._active is None:
                raise RuntimeError("reset required")
            return self._active.step(action)

        def close(self):
            if self._active is not None:
                self._active.close()
                self._active = None
            self._prototype.close()

    torch.set_num_threads(1)
    env = CyclingPublicScenes()
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    try:
        model = PPO("MultiInputPolicy", env, seed=policy_seed, n_steps=128,
                    batch_size=64, n_epochs=2, learning_rate=3e-4,
                    device="cpu", verbose=0)
        model.learn(total_timesteps=timesteps)
        frozen = evaluate_frozen()
        trained = _score_policy(lambda _seed: lambda obs: model.predict(obs, deterministic=True)[0])
    finally:
        env.close()
    return {"schema": SCHEMA, "status": "experimental-not-accepted",
            "backend": "stable-baselines3-ppo-cpu", "sb3_version": SB3_PIN,
            "gymnasium_version": GYMNASIUM_PIN,
            "train_seeds": list(TRAIN_SEEDS), "heldout_seeds": list(HELDOUT_SEEDS),
            "timesteps": timesteps, "policy_seed": policy_seed,
            "frozen_no_learning": frozen, "trained_ppo": trained,
            "delta_success_rate": round(trained["success_rate"] - frozen["success_rate"], 9),
            "wall_seconds": round(time.perf_counter() - start_wall, 4),
            "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
            "claims": ["PUBLIC_SYNTHETIC_ONLY", "NO_PRODUCTION_INFERENCE", "NO_REAL_ACTUATION"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-sb3", action="store_true", help="explicitly load optional ML dependencies")
    parser.add_argument("--timesteps", type=int, default=2048)
    parser.add_argument("--policy-seed", type=int, default=2026)
    args = parser.parse_args(argv)
    if not args.native_sb3:
        _validate_splits()
        result = {"schema": SCHEMA, "mode": "stdlib-frozen-only",
                  "heldout_seeds": list(HELDOUT_SEEDS), "frozen_no_learning": evaluate_frozen(),
                  "note": "No neural training without --native-sb3"}
    else:
        result = run_native(timesteps=args.timesteps, policy_seed=args.policy_seed)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"benchmark refused: {exc}", file=sys.stderr)
        sys.exit(2)
