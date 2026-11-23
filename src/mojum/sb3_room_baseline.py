"""Opt-in CPU SB3 benchmark for PUBLIC synthetic point-room fixtures (#690).

No PRIVATE journal, hardware, network, core policy changes or mandatory imports.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import operator

from .gym_room import PointRoomEpisode
from .world_room import Room

SCHEMA = "mojum.benchmark.sb3-point-room/1"
MAX_STEPS = 64
MAX_TIMESTEPS = 4096


@dataclass(frozen=True)
class Scene:
    name: str
    room: Room
    start: tuple[float, float]
    goal: tuple[float, float]

    def fingerprint(self) -> str:
        payload = [self.room.width, self.room.height, self.room.obstacles,
                   self.start, self.goal]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()

    def episode(self) -> PointRoomEpisode:
        return PointRoomEpisode(self.room, start=self.start, goal=self.goal,
                                max_steps=MAX_STEPS)


TRAIN = (
    Scene("train-open-east", Room(4., 4.), (.75, 1.), (2.75, 1.)),
    Scene("train-open-north", Room(4., 4.), (1., .75), (1., 2.75)),
)
HELDOUT = (
    Scene("heldout-open-diagonal", Room(4., 4.), (.75, .75), (2.75, 2.75)),
    Scene("heldout-barrier-east", Room(4., 4., ((1.5, .5, .25, 2.),)),
          (.75, 1.), (2.75, 1.)),
    Scene("heldout-barrier-north", Room(4., 4., ((.5, 1.5, 2., .25),)),
          (1., .75), (1., 2.75)),
)


def validate_split() -> None:
    """Reject train/eval scene leakage before evaluation or optional training."""
    all_scenes = TRAIN + HELDOUT
    names = [s.name for s in all_scenes]
    hashes = [s.fingerprint() for s in all_scenes]
    if (not TRAIN or not HELDOUT or len(set(names)) != len(names)
            or len(set(hashes)) != len(hashes)):
        raise ValueError("train/held-out scenes overlap")
    for scene in all_scenes:
        scene.episode()


def _seed(value: int) -> int:
    # Intentionally uint32-compatible for SB3/NumPy RNG; Gymnasium itself supports larger seeds.
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError("invalid bounded seed")
    return value


def idle_policy(_observation: dict) -> int:
    return 0


def greedy_policy(observation: dict) -> int:
    """Frozen no-learning control: only current observation, no wall map."""
    x, y = observation["position"]
    gx, gy = observation["goal"]
    dx, dy = gx - x, gy - y
    if abs(dx) >= abs(dy) and abs(dx) > 1e-12:
        return 1 if dx > 0 else 2
    if abs(dy) > 1e-12:
        return 3 if dy > 0 else 4
    return 0


def run_episode(scene: Scene, policy, *, seed: int) -> dict:
    """Evaluate public fixture without any hidden map or private state."""
    _seed(seed)
    env = scene.episode()
    obs, _info = env.reset(seed=seed)
    cumulative = 0.
    blocked = 0
    while True:
        action = policy(obs)
        if type(action) is bool:
            raise ValueError("policy returned bool")
        try:
            action = operator.index(action)
        except TypeError as exc:
            raise ValueError("policy returned non-integer action") from exc
        if not 0 <= action <= 4:
            raise ValueError("policy returned invalid action")
        obs, reward, terminated, truncated, info = env.step(action)
        cumulative += reward
        blocked += int(info["blocked"])
        if terminated or truncated:
            return {"scene": scene.name, "steps": obs["tick"],
                    "success": bool(terminated), "blocked": blocked,
                    "return": round(cumulative, 6)}


def evaluate(policy, *, seed: int = 7) -> dict:
    _seed(seed)
    validate_split()
    episodes = [run_episode(scene, policy, seed=seed)
                for scene in HELDOUT]
    return {"episodes": episodes,
            "success_count": sum(int(x["success"]) for x in episodes),
            "episode_count": len(episodes),
            "total_steps": sum(x["steps"] for x in episodes),
            "total_blocked": sum(x["blocked"] for x in episodes),
            "total_return": round(sum(x["return"] for x in episodes), 6)}


def train_ppo(*, seed: int = 7, timesteps: int = 256):
    """In-memory CPU PPO on TRAIN only; requires separately installed packages."""
    _seed(seed)
    if type(timesteps) is not int or not 64 <= timesteps <= MAX_TIMESTEPS:
        raise ValueError("training timesteps outside 64..4096")
    validate_split()
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.vec_env import DummyVecEnv
        from .gym_room import make_gymnasium_env
        import torch
    except ImportError as exc:
        raise RuntimeError("SB3 mode requires separately installed stable-baselines3, "
                           "gymnasium and torch") from exc
    torch.set_num_threads(1)
    env = DummyVecEnv([
        (lambda scene=scene: make_gymnasium_env(
            scene.room, start=scene.start, goal=scene.goal,
            max_steps=MAX_STEPS)) for scene in TRAIN
    ])
    try:
        env.seed(seed)
        model = PPO("MultiInputPolicy", env, device="cpu", seed=seed,
                    verbose=0, n_steps=32, batch_size=32, n_epochs=1,
                    learning_rate=3e-4, policy_kwargs={"net_arch": [32, 32]})
        model.learn(total_timesteps=timesteps)
        return model, int(model.num_timesteps)
    finally:
        env.close()


def report(*, mode: str = "control", seed: int = 7,
           timesteps: int = 256) -> dict:
    _seed(seed)
    validate_split()
    if mode not in ("control", "sb3"):
        raise ValueError("unsupported benchmark mode")
    if type(timesteps) is not int or not 64 <= timesteps <= MAX_TIMESTEPS:
        raise ValueError("training timesteps outside budget")
    result = {"schema": SCHEMA, "mode": mode,
              "suite": "synthetic-fixed-scene-v1", "seed": seed,
              "train_scene_sha256": [s.fingerprint() for s in TRAIN],
              "heldout_scene_sha256": [s.fingerprint() for s in HELDOUT],
              "seed_changes_geometry": False,
              "idle": evaluate(idle_policy, seed=seed),
              "greedy": evaluate(greedy_policy, seed=seed)}
    if mode == "sb3":
        model, actual = train_ppo(seed=seed, timesteps=timesteps)

        def frozen_sb3(observation):
            action, _state = model.predict(observation, deterministic=True)
            return operator.index(action.item() if hasattr(action, "item") else action)

        result["sb3"] = evaluate(frozen_sb3, seed=seed)
        result["training_timesteps_requested"] = timesteps
        result["training_timesteps_actual"] = actual
        result["training_isolation"] = "train-scenes-only;fresh-heldout-episodes"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("control", "sb3"), default="control")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-timesteps", type=int, default=256)
    args = parser.parse_args(argv)
    print(json.dumps(report(mode=args.mode, seed=args.seed,
                            timesteps=args.train_timesteps),
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
