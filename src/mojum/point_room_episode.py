"""Dependency-free, deterministic Gymnasium-shaped point-room episodes.

This is a synthetic fixture, not a physical simulator or gymnasium.Env.
"""
from __future__ import annotations

import math
import random

from mojum.world_room import Room, advance, initial_state

SCHEMA = "mojum.point-room.episode/1"


class PointRoomEpisode:
    """One bounded, deterministic point-room episode.

    Actions are (dx, dy) displacement pairs in metres. A blocked move consumes
    a tick. Reaching the target terminates; exhausting max_steps truncates.
    Reward is a documented synthetic benchmark signal, not organism affect.
    """

    def __init__(self, room: Room, *, max_steps: int = 100,
                 max_action_m: float = 0.25, goal_radius_m: float = 0.1):
        if not isinstance(room, Room):
            raise TypeError("room must be Room")
        if type(max_steps) is not int or not 1 <= max_steps <= 1_000_000:
            raise ValueError("invalid max_steps")
        for name, value in (("max_action_m", max_action_m),
                            ("goal_radius_m", goal_radius_m)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid " + name)
        self.room = room
        self.max_steps = max_steps
        self.max_action_m = float(max_action_m)
        self.goal_radius_m = float(goal_radius_m)
        self._state = None
        self._goal = None
        self._done = False

    @staticmethod
    def _position(value, name):
        if type(value) not in (tuple, list) or len(value) != 2:
            raise ValueError("invalid " + name)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
            raise ValueError("invalid " + name)
        return tuple(value)

    def reset(self, *, seed: int = 0, start=None, goal=None):
        """Return (observation, info). Same seed and geometry yield same episode."""
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError("invalid seed")
        rng = random.Random(seed)
        if start is None:
            start = self._sample_position(rng)
        else:
            start = self._position(start, "start")
        if goal is None:
            goal = self._sample_position(rng)
        else:
            goal = self._position(goal, "goal")
        # Reuse canonical world validation, including obstacle boundary rules.
        state = initial_state(self.room, *start)
        initial_state(self.room, *goal)
        self._state = state
        self._goal = goal
        self._done = False
        return self._observation(), {"schema": SCHEMA, "seed": seed,
                                     "revision": state.revision}

    def _sample_position(self, rng):
        # Fixed attempt budget: impossible/mostly blocked rooms fail promptly.
        for _ in range(1024):
            candidate = (rng.uniform(0, self.room.width),
                         rng.uniform(0, self.room.height))
            try:
                initial_state(self.room, *candidate)
            except ValueError:
                continue
            return candidate
        raise ValueError("unable to sample free position")

    def _observation(self):
        return {"position": [self._state.x, self._state.y],
                "goal": list(self._goal), "tick": self._state.tick}

    def step(self, action):
        """Return (observation, reward, terminated, truncated, info)."""
        if self._state is None:
            raise RuntimeError("reset required")
        if self._done:
            raise RuntimeError("episode finished; reset required")
        dx, dy = self._position(action, "action")
        if math.hypot(dx, dy) > self.max_action_m:
            raise ValueError("action exceeds max_action_m")
        state, world_info = advance(
            self.room, self._state, tick=self._state.tick + 1,
            dx=dx, dy=dy, expected_revision=self._state.revision)
        self._state = state
        distance = math.hypot(state.x - self._goal[0], state.y - self._goal[1])
        terminated = distance <= self.goal_radius_m
        truncated = not terminated and state.tick >= self.max_steps
        self._done = terminated or truncated
        reward = 1.0 if terminated else (-0.01 if world_info["blocked"] else 0.0)
        return self._observation(), reward, terminated, truncated, {
            "schema": SCHEMA, "blocked": world_info["blocked"],
            "revision": state.revision, "distance_to_goal_m": distance,
            "physical_contact": False}
