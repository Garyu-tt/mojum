"""Optional Gymnasium-shaped *fixture* facade; no Gymnasium dependency.

This exposes the existing deterministic two-dimensional point-room, not
MuJoCo, a neural controller, or a physical robot. No canonical Aquarium
journal is read or written. It intentionally does not inherit gym.Env:
a separately installed Gymnasium adapter can wrap this stable contract.
"""
from __future__ import annotations

import math

from .world_room import Room, advance, initial_state


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


class PointRoomEnv:
    """Goal-free, bounded episode with Gymnasium-style reset/step tuples.

    Zero rewards are deliberate: do not silently teach an engineered goal.
    A policy can use the measured blocked flag in an experimental harness.
    """

    def __init__(
        self,
        room: Room,
        *,
        start: tuple[float, float],
        max_steps: int = 256,
        max_step_m: float = 0.25,
    ) -> None:
        if not isinstance(room, Room):
            raise ValueError("room must be Room")
        if type(start) not in (tuple, list) or len(start) != 2:
            raise ValueError("start must be an (x, y) pair")
        if type(max_steps) is not int or not 1 <= max_steps <= 100_000:
            raise ValueError("max_steps must be 1..100000")
        if not _finite_number(max_step_m) or not 0 < max_step_m <= 0.25:
            raise ValueError("max_step_m must be finite and within (0, 0.25]")
        # Validate once, pin the geometry and do not mutate the caller's room.
        initial_state(room, start[0], start[1])
        self.room = room
        self.start = (start[0], start[1])
        self.max_steps = max_steps
        # Keep the constructor's episode budget as a hard upper bound.
        self._configured_max_steps = max_steps
        self.max_step_m = max_step_m
        self._configured_max_step_m = max_step_m
        self._state = None
        self._steps = 0

    def _observation(self) -> dict:
        state = self._state
        assert state is not None
        return {
            "tick": state.tick,
            "x": state.x,
            "y": state.y,
            "revision": state.revision,
            "synthetic": True,
        }

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[dict, dict]:
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2**64):
            raise ValueError("seed must be a nonnegative 64-bit integer")
        if options is not None and (type(options) is not dict or options):
            raise ValueError("only empty reset options are supported")
        self._state = initial_state(self.room, *self.start)
        self._steps = 0
        return self._observation(), {
            "schema": "mojum.point-room-episode/1",
            "seed": seed,
            "physical_contact": False,
        }

    def step(self, action: tuple[float, float]) -> tuple[dict, float, bool, bool, dict]:
        if self._state is None:
            raise RuntimeError("reset required before step")
        if (type(self.max_steps) is not int
                or not 1 <= self.max_steps <= self._configured_max_steps):
            raise ValueError("episode step budget cannot exceed configured limit")
        if self._steps >= self.max_steps:
            raise RuntimeError("reset required after truncation")
        if type(action) not in (tuple, list) or len(action) != 2:
            raise ValueError("action must be (dx, dy)")
        dx, dy = action
        if not _finite_number(dx) or not _finite_number(dy):
            raise ValueError("action coordinates must be finite non-bool numbers")
        # Recheck the public configuration and the hard ceiling at every step.
        # Caller mutation must not let an experimental policy exceed 0.25 m.
        if (not _finite_number(self.max_step_m)
                or not 0 < self.max_step_m <= self._configured_max_step_m):
            raise ValueError("invalid per-tick displacement budget")
        if math.hypot(dx, dy) > self.max_step_m:
            raise ValueError("action exceeds per-tick displacement budget")
        next_state, observed = advance(
            self.room, self._state, tick=self._state.tick + 1,
            dx=dx, dy=dy, expected_revision=self._state.revision,
        )
        self._state = next_state
        self._steps += 1
        info = {
            "schema": "mojum.point-room-episode/1",
            "blocked": observed["blocked"],
            "physical_contact": False,
            "provenance": observed["provenance"],
            "revision": next_state.revision,
        }
        return self._observation(), 0.0, False, self._steps == self.max_steps, info
