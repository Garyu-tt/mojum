"""Opt-in, offline Gymnasium bridge for Mojum's deterministic point-room.

The stdlib episode does not open journals, change the room simulator, use a
network or load Gymnasium. Gymnasium is imported only by make_gymnasium_env().
"""
from __future__ import annotations

import math
import operator

from .world_room import Room, advance, initial_state

SCHEMA = "mojum.gym.point-room/1"
# Deterministic action IDs; there are no direct physics or motor commands.
ACTIONS = ((0.0, 0.0), (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))


def _finite_scalar(value):
    """Reject bool, nonfinite and integer overflow with a stable validation result."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _point(value, label):
    if (type(value) not in (tuple, list) or len(value) != 2
            or not all(_finite_scalar(x) for x in value)):
        raise ValueError(f"invalid {label}")
    return tuple(float(x) for x in value)


class PointRoomEpisode:
    """Finite fixed-start episode; no randomness or private state in observations."""

    def __init__(self, room: Room, *, start=(1.0, 1.0), goal=(3.0, 3.0),
                 step_m=0.25, goal_radius_m=0.125, max_steps=64):
        if (not isinstance(room, Room) or room.width > 1000 or room.height > 1000):
            raise ValueError("room must be a bounded point-room")
        self.room = room
        self.start = _point(start, "start")
        self.goal = _point(goal, "goal")
        # Validate both endpoints against the authoritative room collision rules.
        initial_state(room, *self.start)
        initial_state(room, *self.goal)
        if (not _finite_scalar(step_m)
                or not 0 < step_m <= 1):
            raise ValueError("invalid step size")
        if (not _finite_scalar(goal_radius_m)
                or not 0 <= goal_radius_m <= 1):
            raise ValueError("invalid goal radius")
        if type(max_steps) is not int or not 1 <= max_steps <= 10000:
            raise ValueError("invalid episode horizon")
        self.step_m = float(step_m)
        self.goal_radius_m = float(goal_radius_m)
        self.max_steps = max_steps
        # Keep constructor budgets authoritative across a mutable Python facade.
        self._fixed_step_m = self.step_m
        self._fixed_goal_radius_m = self.goal_radius_m
        self._fixed_max_steps = self.max_steps
        self._fixed_start = self.start
        self._fixed_goal = self.goal
        self._fixed_room = self.room
        self._state = None
        self._done = False
        self._last_blocked = False
        self._seed = None

    def _assert_configuration(self):
        """Reject facade mutation before a reset or reward-producing step."""
        if (self.room is not self._fixed_room
                or type(self.start) is not tuple or self.start != self._fixed_start
                or type(self.goal) is not tuple or self.goal != self._fixed_goal
                or type(self.step_m) is not float or self.step_m != self._fixed_step_m
                or type(self.max_steps) is not int or self.max_steps != self._fixed_max_steps
                or type(self.goal_radius_m) is not float
                or self.goal_radius_m != self._fixed_goal_radius_m):
            raise ValueError("episode configuration mutated after construction")

    def _observation(self):
        s = self._state
        return {"position": [float(s.x), float(s.y)],
                "goal": list(self.goal), "blocked": int(self._last_blocked),
                "tick": s.tick}

    def _info(self):
        return {"schema": SCHEMA, "revision": self._state.revision,
                "provenance": "deterministic-point-room-fixture/v1",
                "physical_contact": False, "seed": self._seed}

    def reset(self, *, seed=None, options=None):
        if (seed is not None and (type(seed) is not int or seed < 0)):
            raise ValueError("invalid seed")
        if options is not None and (type(options) is not dict or options):
            raise ValueError("reset options are not supported")
        # Seed is recorded for Gymnasium compatibility; fixed room has no RNG.
        self._assert_configuration()
        state = initial_state(self.room, *self.start)
        self._state = state
        self._seed = seed
        self._done = False
        self._last_blocked = False
        return self._observation(), self._info()

    def step(self, action):
        if self._state is None:
            raise RuntimeError("reset must precede step")
        if self._done:
            raise RuntimeError("episode ended; reset required")
        # Do not let a policy/harness change motion, horizon, start or reward goal.
        # Fail before advancing the authoritative point-room state.
        self._assert_configuration()
        if isinstance(action, bool):
            raise ValueError("invalid action")
        try:
            index = operator.index(action)  # includes numpy.integer / Discrete.sample()
        except TypeError as exc:
            raise ValueError("invalid action") from exc
        if not 0 <= index < len(ACTIONS):
            raise ValueError("invalid action")
        ux, uy = ACTIONS[index]
        s, outcome = advance(self.room, self._state, tick=self._state.tick + 1,
                             dx=ux * self.step_m, dy=uy * self.step_m,
                             expected_revision=self._state.revision)
        self._state = s
        self._last_blocked = outcome["blocked"]
        reached = math.hypot(s.x - self.goal[0], s.y - self.goal[1]) <= self.goal_radius_m
        terminated = bool(reached)
        truncated = bool(not terminated and s.tick >= self.max_steps)
        self._done = terminated or truncated
        reward = 1.0 if terminated else (-0.05 if self._last_blocked else -0.01)
        info = self._info()
        info["blocked"] = self._last_blocked
        return self._observation(), reward, terminated, truncated, info


def make_gymnasium_env(room: Room, **episode_kwargs):
    """Construct actual gymnasium.Env only when caller explicitly opts in.

    Install gymnasium separately; the baseline and import of this module stay
    dependency-free. Stable-Baselines3 is deliberately not imported here.
    """
    try:
        import gymnasium as gym
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("optional Gymnasium not installed") from exc

    episode = PointRoomEpisode(room, **episode_kwargs)

    class GymnasiumPointRoomEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self._episode = episode
            self.action_space = gym.spaces.Discrete(len(ACTIONS))
            high = np.asarray([float(room.width), float(room.height)], dtype=np.float64)
            self.observation_space = gym.spaces.Dict({
                "position": gym.spaces.Box(low=np.zeros(2, dtype=np.float64),
                                           high=high, dtype=np.float64),
                "goal": gym.spaces.Box(low=np.zeros(2, dtype=np.float64),
                                       high=high, dtype=np.float64),
                "blocked": gym.spaces.Discrete(2),
                "tick": gym.spaces.Discrete(episode.max_steps + 1),
            })

        @staticmethod
        def _convert(obs):
            return {**obs, "position": np.asarray(obs["position"], dtype=np.float64),
                    "goal": np.asarray(obs["goal"], dtype=np.float64)}

        def reset(self, *, seed=None, options=None):
            obs, info = self._episode.reset(seed=seed, options=options)
            super().reset(seed=seed)
            return self._convert(obs), info

        def step(self, action):
            obs, reward, terminated, truncated, info = self._episode.step(action)
            return self._convert(obs), reward, terminated, truncated, info

    return GymnasiumPointRoomEnv()
