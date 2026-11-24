"""Opt-in *actual* Gymnasium Env wrapper around the stdlib point-room fixture.

Importing this module is stdlib-safe; constructing the wrapper requires a
separately installed Gymnasium+NumPy. This never opens an Aquarium journal.
"""
from __future__ import annotations

import math

from .point_room_env import PointRoomEnv


def make_gymnasium_point_room(room, *, start, max_steps=256, max_step_m=0.25):
    """Build a Gymnasium Env without making Gymnasium a core dependency."""
    core = PointRoomEnv(
        room, start=start, max_steps=max_steps, max_step_m=max_step_m,
    )
    try:
        import gymnasium as gym
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Gymnasium adapter is optional; install gymnasium and numpy separately"
        ) from exc

    class GymPointRoom(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self._core = core
            # Inscribe a square into the physical per-tick displacement circle,
            # so ALL Box samples satisfy the core's Euclidean budget.
            bound = max_step_m * (1.0 - 1e-6) / math.sqrt(2.0)
            self.action_space = gym.spaces.Box(
                low=-bound, high=bound, shape=(2,), dtype=np.float32,
            )
            self.observation_space = gym.spaces.Dict({
                "position": gym.spaces.Box(
                    low=np.array([0.0, 0.0], dtype=np.float64),
                    high=np.array([float(room.width), float(room.height)],
                                  dtype=np.float64),
                    dtype=np.float64,
                ),
                "tick": gym.spaces.Discrete(max_steps + 1),
            })

        @staticmethod
        def _obs(value):
            return {
                "position": np.array([value["x"], value["y"]],
                                     dtype=np.float64),
                "tick": value["tick"],
            }

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            observation, info = self._core.reset(seed=seed, options=options)
            return self._obs(observation), info

        def step(self, action):
            arr = np.asarray(action)
            if (arr.shape != (2,) or arr.dtype.kind not in "fiu"
                    or not np.all(np.isfinite(arr))):
                raise ValueError("Gymnasium action must be two finite numeric values")
            # Do not let numpy scalar dtypes bypass the stdlib core validator.
            actual = (float(arr[0]), float(arr[1]))
            # Reject the original numeric values before any float32 cast.
            # Otherwise, a float64 just outside Box.high can round down
            # to its float32 endpoint and bypass the advertised action space.
            if not all(float(lo) <= value <= float(hi)
                       for value, lo, hi in zip(
                           actual, self.action_space.low, self.action_space.high
                       )):
                raise ValueError("action is outside the advertised Gymnasium Box")
            if not self.action_space.contains(
                np.asarray(actual, dtype=np.float32)
            ):
                raise ValueError("action is outside the advertised Gymnasium Box")
            observation, reward, terminated, truncated, info = self._core.step(actual)
            return self._obs(observation), reward, terminated, truncated, info

    return GymPointRoom()
