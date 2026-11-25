"""Opt-in, CPU-only Stable-Baselines3 held-out benchmark for synthetic Mojum rooms.

No external import, file I/O, training or network is triggered by importing this
module. Only ``--run`` loads Gymnasium/SB3/PyTorch. No Noosphere/organism state.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import operator

from .world_room import Room

SCHEMA = "mojum.benchmark.sb3-point-room/1"
MAX_TRAIN_STEPS = 8192
ROLLOUT_STEPS = 64
TRAIN_SEED = 20261123
EPISODE_STEPS = 48


@dataclass(frozen=True)
class Scene:
    name: str
    obstacles: tuple[tuple[float, float, float, float], ...]

    def room(self) -> Room:
        return Room(4.0, 4.0, self.obstacles)

    def fingerprint(self) -> str:
        payload = json.dumps([4.0, 4.0, self.obstacles], separators=(",", ":"))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()


# Public, deliberately synthetic geometry. Evaluation scenes are not training
# scenes, and the agent never observes obstacle coordinates directly.
TRAIN_SCENES = (
    Scene("train-clear", ()),
    Scene("train-left-barrier", ((1.5, 0.5, 0.25, 2.0),)),
    Scene("train-horizontal-barrier", ((0.6, 2.0, 2.5, 0.2),)),
    Scene("train-right-barrier", ((2.0, 1.0, 0.3, 2.0),)),
)
EVAL_SCENES = (
    Scene("heldout-narrow", ((1.2, 1.0, 0.3, 1.8),)),
    Scene("heldout-offset", ((2.1, 0.4, 0.3, 2.0),)),
    Scene("heldout-low-horizontal", ((0.5, 1.5, 2.0, 0.2),)),
    Scene("heldout-high-horizontal", ((1.0, 2.1, 2.0, 0.3),)),
)


def validate_config(timesteps: int, seed: int) -> None:
    # PPO with four workers and 64 steps each: bound and avoid implicit rollover.
    if type(timesteps) is not int or not 256 <= timesteps <= MAX_TRAIN_STEPS or timesteps % 256:
        raise ValueError("timesteps must be an integer multiple of 256, 256..8192")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be uint32")
    assert len({s.fingerprint() for s in TRAIN_SCENES + EVAL_SCENES}) == 8


def plan(timesteps: int = 2048, seed: int = TRAIN_SEED) -> dict:
    validate_config(timesteps, seed)
    return {
        "schema": SCHEMA,
        "kind": "public-synthetic-offline-only",
        "run_requested": False,
        "seed": seed,
        "timesteps": timesteps,
        "parallel_envs": len(TRAIN_SCENES),
        "max_episode_steps": EPISODE_STEPS,
        "training_scenes": [{"name": s.name, "sha256": s.fingerprint()} for s in TRAIN_SCENES],
        "heldout_scenes": [{"name": s.name, "sha256": s.fingerprint()} for s in EVAL_SCENES],
        "policy": "PPO/MultiInputPolicy; CPU; initialized-frozen vs same weights after learning",
        "note": "scene reset seeds are provenance only; scene geometry is explicitly held out",
    }


def _make_env(scene: Scene):
    from .gym_room import make_gymnasium_env
    return make_gymnasium_env(scene.room(), start=(0.5, 0.5), goal=(3.5, 3.5),
                              step_m=0.5, goal_radius_m=0.26, max_steps=EPISODE_STEPS)


def _discrete_action(action) -> int:
    """Reject non-scalar and invalid policy actions; don't silently clip."""
    if isinstance(action, bool):
        raise ValueError("invalid predicted action")
    try:
        a = operator.index(action)
    except TypeError:
        # SB3 can return a zero-dimensional NumPy array.
        if getattr(action, "shape", None) == () and hasattr(action, "item"):
            a = operator.index(action.item())
        else:
            raise ValueError("predicted action must be a scalar integer") from None
    if not 0 <= a <= 4:
        raise ValueError("predicted action outside Discrete(5)")
    return a


def evaluate(model, scenes=EVAL_SCENES, *, env_factory=_make_env, seed=TRAIN_SEED):
    """Evaluate without gradients or training. Caller may supply a stdlib test factory."""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be uint32")
    if not scenes or len(scenes) > 16 or len({s.fingerprint() for s in scenes}) != len(scenes):
        raise ValueError("invalid evaluation scenes")
    results = []
    for i, scene in enumerate(scenes):
        env = env_factory(scene)
        try:
            obs, _ = env.reset(seed=(seed + 10000 + i) % 2**32)
            total, blocked, success, steps = 0.0, 0, False, 0
            for steps in range(1, EPISODE_STEPS + 1):
                raw_action, _ = model.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(_discrete_action(raw_action))
                # Reject malformed upstream output before claiming a measured score.
                # The parent point-room emits only bounded synthetic rewards.
                if type(reward) not in (int, float) or not -1.0 <= reward <= 1.0:
                    raise ValueError("invalid synthetic environment reward")
                if (type(terminated) is not bool or type(truncated) is not bool
                        or (terminated and truncated)):
                    raise ValueError("invalid environment termination flags")
                if type(info) is not dict or type(info.get("blocked")) is not bool:
                    raise ValueError("invalid environment blocked metadata")
                total += float(reward)
                blocked += int(info["blocked"])
                if terminated or truncated:
                    success = bool(terminated)
                    break
            else:
                raise RuntimeError("environment exceeded its declared horizon")
            results.append({"scene": scene.name, "success": success,
                            "steps": steps, "blocked_steps": blocked,
                            "return": round(total, 6)})
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()
    return {"episodes": results, "successes": sum(r["success"] for r in results),
            "mean_return": round(sum(r["return"] for r in results) / len(results), 6),
            "mean_blocked_steps": round(sum(r["blocked_steps"] for r in results) / len(results), 6)}


def run(timesteps=2048, seed=TRAIN_SEED) -> dict:
    """Explicit CPU/PPO execution. No weight save, remote service, or automatic promotion."""
    report = plan(timesteps, seed)
    try:
        import torch
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import DummyVecEnv
    except ImportError as exc:
        raise RuntimeError("SB3 is opt-in: install pinned Gymnasium, SB3, and CPU PyTorch") from exc
    torch.set_num_threads(1)  # avoid unbounded thread fan-out on shared CPU hosts
    env = DummyVecEnv([lambda scene=scene: _make_env(scene) for scene in TRAIN_SCENES])
    try:
        model = PPO("MultiInputPolicy", env, device="cpu", seed=seed,
                    n_steps=ROLLOUT_STEPS, batch_size=64, n_epochs=2,
                    policy_kwargs={"net_arch": {"pi": [32, 32], "vf": [32, 32]}},
                    verbose=0)
        # Same initialized weights are evaluated before any gradient update.
        frozen = evaluate(model, seed=seed)
        model.learn(total_timesteps=timesteps, reset_num_timesteps=True)
        learned = evaluate(model, seed=seed)
    finally:
        env.close()
    report["run_requested"] = True
    report["frozen"] = frozen
    report["learned"] = learned
    report["delta_successes"] = learned["successes"] - frozen["successes"]
    report["delta_mean_return"] = round(learned["mean_return"] - frozen["mean_return"], 6)
    report["limitations"] = "small synthetic suite; no causal generalization or organism learning claim"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="CPU-first PUBLIC synthetic SB3 baseline (opt-in)")
    parser.add_argument("--run", action="store_true", help="explicitly train/evaluate with optional SB3")
    parser.add_argument("--timesteps", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=TRAIN_SEED)
    args = parser.parse_args(argv)
    result = run(args.timesteps, args.seed) if args.run else plan(args.timesteps, args.seed)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
