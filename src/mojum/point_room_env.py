"""Deterministic, dependency-free point-room episode adapter.

This is a small Gymnasium-like API over the canonical synthetic point-room.
It is not a Gymnasium Env implementation and does not add a runtime dependency.
"""
from __future__ import annotations

import math
import random
from typing import Any

from .world_room import Room, RoomState, advance, initial_state

SCHEMA = "mojum.point-room-episode/1"
MAX_EPISODE_STEPS = 100_000
MAX_SEED = (1 << 63) - 1


def _position(value: Any, *, field: str) -> tuple[int | float, int | float]:
    if type(value) not in (tuple, list) or len(value) != 2:
        raise ValueError(f"{field} must be a two-item tuple or list")
    x, y = value
    if type(x) not in (int, float) or type(y) not in (int, float):
        raise ValueError(f"{field} coordinates must be native int or float values")
    try:
        finite = math.isfinite(x) and math.isfinite(y)
    except (OverflowError, TypeError, ValueError):
        finite = False
    if not finite:
        raise ValueError(f"{field} coordinates must be finite")
    return x, y


def _positive_finite(value: Any, *, field: str) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a native int or float")
    try:
        valid = math.isfinite(value) and value > 0
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"{field} must be finite and positive")
    return value


class PointRoomEnv:
    """Bounded episode wrapper around world_room.advance.

    Actions are (dx, dy) displacements in metres. Observations and info
    are fresh dictionaries, so caller mutation cannot alter internal state.
    A configured goal produces terminated=True; exhausting the episode
    budget produces truncated=True. A terminal state takes precedence if
    both conditions happen on the same step.
    """

    def __init__(
        self,
        room: Room,
        *,
        start_position: tuple[int | float, int | float],
        spawn_positions: tuple[tuple[int | float, int | float], ...] | None = None,
        goal_position: tuple[int | float, int | float] | None = None,
        goal_tolerance_m: int | float = 0.05,
        max_episode_steps: int = 200,
        max_action_m: int | float = 1.0,
    ) -> None:
        if type(room) is not Room:
            raise ValueError("room must be an exact canonical Room instance")
        if type(max_episode_steps) is not int or not 1 <= max_episode_steps <= MAX_EPISODE_STEPS:
            raise ValueError("max_episode_steps is outside the supported integer range")
        self._max_episode_steps = max_episode_steps
        self._max_action_m = _positive_finite(max_action_m, field="max_action_m")
        if self._max_action_m > 1000.0:
            raise ValueError("max_action_m exceeds the canonical room displacement budget")
        self._goal_tolerance_m = _positive_finite(goal_tolerance_m, field="goal_tolerance_m")
        self._room = room

        start = _position(start_position, field="start_position")
        candidates = (start,) if spawn_positions is None else spawn_positions
        if type(candidates) not in (tuple, list) or not 1 <= len(candidates) <= 256:
            raise ValueError("spawn_positions must contain 1..256 positions")
        checked: list[tuple[int | float, int | float]] = []
        for candidate in candidates:
            point = _position(candidate, field="spawn position")
            initial_state(room, point[0], point[1])
            if point in checked:
                raise ValueError("spawn_positions must not contain duplicates")
            checked.append(point)
        if start not in checked:
            raise ValueError("start_position must be included in spawn_positions")
        self._spawn_positions = tuple(checked)

        if goal_position is None:
            self._goal_position = None
        else:
            goal = _position(goal_position, field="goal_position")
            initial_state(room, goal[0], goal[1])
            self._goal_position = goal

        self._rng = random.Random()
        self._seed: int | None = None
        self._state: RoomState | None = None
        self._episode_steps = 0
        self._done = True
        self._last_info: dict[str, Any] = {}

    @property
    def state(self) -> RoomState | None:
        """Return the immutable canonical state for diagnostics."""
        return self._state

    @property
    def episode_steps(self) -> int:
        return self._episode_steps

    def _observation(self) -> dict[str, Any]:
        assert self._state is not None
        return {
            "schema": SCHEMA,
            "tick": self._state.tick,
            "x": self._state.x,
            "y": self._state.y,
            "revision": self._state.revision,
        }

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Start a new episode; a supplied seed reproduces spawn selection."""
        if seed is not None:
            if type(seed) is not int or not 0 <= seed <= MAX_SEED:
                raise ValueError("seed must be a native non-negative 63-bit integer")
            self._rng = random.Random(seed)
            self._seed = seed
        if options is None:
            options = {}
        if type(options) is not dict or any(type(k) is not str for k in options):
            raise ValueError("options must be a native dict with string keys")
        if set(options) - {"start_position"}:
            raise ValueError("unsupported reset option")
        if "start_position" in options:
            position = _position(options["start_position"], field="options.start_position")
        else:
            position = self._rng.choice(self._spawn_positions)
        self._state = initial_state(self._room, position[0], position[1])
        self._episode_steps = 0
        self._done = False
        self._last_info = {
            "schema": SCHEMA,
            "seed": self._seed,
            "episode_step": 0,
            "provenance": "PUBLIC_SYNTHETIC",
            "physical_contact": False,
        }
        return self._observation(), dict(self._last_info)

    def step(
        self, action: tuple[int | float, int | float] | list[int | float]
    ) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        """Advance exactly one canonical room tick with a bounded displacement."""
        if self._state is None or self._done:
            raise RuntimeError("reset() must start a new episode before step()")
        dx, dy = _position(action, field="action")
        try:
            magnitude = math.hypot(dx, dy)
        except (OverflowError, TypeError, ValueError):
            raise ValueError("action magnitude is invalid") from None
        if not math.isfinite(magnitude) or magnitude > self._max_action_m:
            raise ValueError("action exceeds max_action_m")

        next_state, room_info = advance(
            self._room,
            self._state,
            tick=self._state.tick + 1,
            dx=dx,
            dy=dy,
            expected_revision=self._state.revision,
        )
        self._state = next_state
        self._episode_steps += 1
        terminated = False
        if self._goal_position is not None:
            gx, gy = self._goal_position
            terminated = math.hypot(next_state.x - gx, next_state.y - gy) <= self._goal_tolerance_m
        truncated = self._episode_steps >= self._max_episode_steps and not terminated
        self._done = terminated or truncated
        info = {
            "schema": SCHEMA,
            "seed": self._seed,
            "episode_step": self._episode_steps,
            "blocked": room_info["blocked"],
            "revision": next_state.revision,
            "provenance": "PUBLIC_SYNTHETIC",
            "physical_contact": False,
        }
        self._last_info = info
        reward = 1.0 if terminated else 0.0
        return self._observation(), reward, terminated, truncated, dict(info)
