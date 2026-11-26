"""Opt-in, bounded RoboPianist dm_env facade and synthetic piano benchmark.

This is not a learned motor policy, a real keyboard sensor or a physical actuator.
No RoboPianist, MuJoCo, NumPy, I/O or network dependency is needed for the
synthetic benchmark. A real environment is loaded only by open_robopianist().
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import random
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

FRAME_SCHEMA = "mojum.robopianist-frame/1"
REPORT_SCHEMA = "mojum.robopianist-benchmark/1"
MAX_ACTION_DIM = 256
MAX_PIANO_KEYS = 88
MAX_GOAL_ITEMS = 1424
MAX_STEPS = 64
_TASK = re.compile(r"RoboPianist-[A-Za-z0-9_-]{1,120}\Z", re.ASCII)


class PianoBridgeError(ValueError):
    """Rejected input, incompatible environment or unsafe benchmark state."""


def _number(value: Any, label: str) -> float:
    if value is None or isinstance(value, (bool, str, bytes, complex, dict, list, tuple)):
        raise PianoBridgeError("invalid " + label)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PianoBridgeError("invalid " + label) from exc
    if not math.isfinite(result):
        raise PianoBridgeError("non-finite " + label)
    return result


def _bounds(values: Any, size: int, label: str) -> tuple[float, ...]:
    if not hasattr(values, "__len__"):
        return (_number(values, label),) * size
    if len(values) != size:
        raise PianoBridgeError("invalid " + label + " length")
    return tuple(_number(v, label) for v in values)


def _validate_action(action: Any, lower: tuple[float, ...], upper: tuple[float, ...]) -> tuple[float, ...]:
    if type(action) not in (list, tuple) or len(action) != len(lower):
        raise PianoBridgeError("invalid action shape")
    out = []
    for index, item in enumerate(action):
        if type(item) not in (float, int):
            raise PianoBridgeError("invalid action type")
        value = _number(item, "action")
        if not lower[index] <= value <= upper[index]:
            raise PianoBridgeError("action outside bounded spec")
        out.append(value)
    return tuple(out)


def _validate_native_action(action: Any, lower: tuple[float, ...], upper: tuple[float, ...]) -> None:
    """Revalidate *converted* backend values before dispatching any step.

    NumPy dtype casts or an explicit array_factory may alter an already-checked
    finite Python vector (e.g. float64 -> float32 overflow to infinity). Never
    trust the pre-cast check as proof of the exact action sent to the backend.
    """
    try:
        shape = getattr(action, "shape", None)
        if shape is not None and tuple(shape) != (len(lower),):
            raise PianoBridgeError("converted action shape mismatch")
        if len(action) != len(lower):
            raise PianoBridgeError("converted action length mismatch")
        for index, (minimum, maximum) in enumerate(zip(lower, upper)):
            value = _number(action[index], "converted action")
            if not minimum <= value <= maximum:
                raise PianoBridgeError("converted action outside bounded spec")
    except (TypeError, IndexError, OverflowError) as exc:
        raise PianoBridgeError("invalid converted action") from exc


def _read_timestep(ts: Any, *, piano_keys: int, tick: int, origin: str, expect_reward: bool) -> dict[str, Any]:
    # Only the current target-key goal is projected; never export raw observation,
    # piano state, camera pixels, actions, notes, private context or MIDI files.
    observation = getattr(ts, "observation", None)
    if not isinstance(observation, dict) or "goal" not in observation:
        raise PianoBridgeError("missing goal observation")
    goal = observation["goal"]
    try:
        size = len(goal)
    except TypeError as exc:
        raise PianoBridgeError("invalid goal shape") from exc
    if not piano_keys <= size <= MAX_GOAL_ITEMS:
        raise PianoBridgeError("goal budget or shape exceeded")
    selected = []
    for index in range(piano_keys):
        value = _number(goal[index], "goal value")
        if not 0.0 <= value <= 1.0:
            raise PianoBridgeError("goal value outside [0, 1]")
        if value >= 0.5:
            selected.append(index)
    last_fn = getattr(ts, "last", None)
    if not callable(last_fn):
        raise PianoBridgeError("missing dm_env last()")
    terminal = last_fn()
    if type(terminal) is not bool:
        raise PianoBridgeError("invalid terminal flag")
    reward = getattr(ts, "reward", None)
    if expect_reward:
        reward = _number(reward, "reward")
    elif reward is not None:
        raise PianoBridgeError("reset returned nonempty reward")
    discount = getattr(ts, "discount", None)
    if discount is not None:
        discount = _number(discount, "discount")
        if not 0 <= discount <= 1:
            raise PianoBridgeError("invalid discount")
    return {
        "schema": FRAME_SCHEMA, "tick": tick, "origin": origin,
        "target_keys": selected, "reward": reward, "discount": discount,
        "terminal": terminal, "virtual_only": True,
    }


class PianoBridge:
    """Bounded dm_env-style adapter. Caller owns and closes the environment.

    The caller must explicitly pass a public synthetic environment or opt in to
    a separately installed RoboPianist environment. This class never installs,
    downloads or starts backends by itself. In case of backend uncertainty the
    session becomes poisoned rather than retrying a possibly-applied step.
    """

    def __init__(self, env: Any, *, piano_keys: int = 88,
                 origin: str = "injected_environment", array_factory=None):
        if type(piano_keys) is not int or not 1 <= piano_keys <= MAX_PIANO_KEYS:
            raise PianoBridgeError("invalid piano key budget")
        if type(origin) is not str or not 1 <= len(origin) <= 128 or not re.fullmatch(r"[a-z0-9:_-]+", origin):
            raise PianoBridgeError("invalid provenance")
        spec = env.action_spec()
        shape = getattr(spec, "shape", None)
        if not isinstance(shape, (tuple, list)) or len(shape) != 1 or type(shape[0]) is not int:
            raise PianoBridgeError("action spec must be 1-D")
        size = shape[0]
        if not 1 <= size <= MAX_ACTION_DIM:
            raise PianoBridgeError("action dimension budget exceeded")
        lower = _bounds(getattr(spec, "minimum", None), size, "minimum")
        upper = _bounds(getattr(spec, "maximum", None), size, "maximum")
        if any(a > b for a, b in zip(lower, upper)):
            raise PianoBridgeError("invalid action range")
        self._env, self._lower, self._upper = env, lower, upper
        self._piano_keys, self._origin = piano_keys, origin
        self._array_factory = array_factory
        self._tick = 0
        self._active = False
        self._poisoned = False
        self._terminal = False

    @property
    def action_size(self) -> int:
        return len(self._lower)

    def reset(self) -> dict[str, Any]:
        if self._poisoned:
            raise PianoBridgeError("unsafe backend state; create a new session")
        self._active = False
        try:
            ts = self._env.reset()
            frame = _read_timestep(ts, piano_keys=self._piano_keys, tick=0,
                                   origin=self._origin, expect_reward=False)
        except Exception:
            self._poisoned = True
            raise PianoBridgeError("backend reset failed or invalid") from None
        if frame["terminal"]:
            self._poisoned = True
            raise PianoBridgeError("terminal reset")
        self._tick = 0
        self._active = True
        self._terminal = False
        return frame

    def step(self, action: list[float] | tuple[float, ...]) -> dict[str, Any]:
        if not self._active or self._poisoned or self._terminal:
            raise PianoBridgeError("session not active")
        if self._tick >= MAX_STEPS:
            raise PianoBridgeError("step budget exhausted")
        vector = _validate_action(action, self._lower, self._upper)
        # Validate before dispatch. Do NOT retry after an ambiguous backend error.
        try:
            if self._array_factory is None:
                numpy = importlib.import_module("numpy")  # only for explicitly used backend
                native_action = numpy.asarray(vector, dtype=self._env.action_spec().dtype)
            else:
                native_action = self._array_factory(vector)
            _validate_native_action(native_action, self._lower, self._upper)
            ts = self._env.step(native_action)
            frame = _read_timestep(ts, piano_keys=self._piano_keys,
                                   tick=self._tick + 1, origin=self._origin,
                                   expect_reward=True)
        except Exception:
            self._poisoned = True
            raise PianoBridgeError("backend step failed or invalid; no retry") from None
        self._tick += 1
        self._terminal = frame["terminal"]
        return frame


def open_robopianist(task: str = "RoboPianist-debug-TwinkleTwinkleLittleStar-v0") -> PianoBridge:
    """Explicit opt-in backend load. Never called by Mojum's core import paths."""
    if type(task) is not str or _TASK.fullmatch(task) is None:
        raise PianoBridgeError("invalid RoboPianist task name")
    try:
        suite = importlib.import_module("robopianist.suite")
    except ImportError as exc:
        raise PianoBridgeError("RoboPianist not installed; backend remains disabled") from exc
    if task not in suite.ALL:
        raise PianoBridgeError("task not in installed RoboPianist suite")
    return PianoBridge(suite.load(task), piano_keys=88, origin="robopianist:dm_env")


@dataclass(frozen=True)
class _SyntheticTimeStep:
    observation: dict[str, list[float]]
    reward: float | None
    discount: float | None
    done: bool = False

    def last(self) -> bool:
        return self.done


class SyntheticPianoEnv:
    """Explicit PUBLIC synthetic fixture; not RoboPianist physics or human piano."""

    def __init__(self, seed: int, *, keys: int = 12, steps: int = 16):
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise PianoBridgeError("invalid fixture seed")
        if type(keys) is not int or not 2 <= keys <= 24:
            raise PianoBridgeError("invalid fixture key budget")
        if type(steps) is not int or not 1 <= steps <= MAX_STEPS:
            raise PianoBridgeError("invalid fixture step count")
        rng = random.Random(seed)
        self.targets = tuple(tuple(sorted(rng.sample(range(keys), rng.randint(1, min(3, keys)))))
                             for _ in range(steps))
        self.keys = keys
        self.count = 0
        self.step_calls = 0

    def action_spec(self):
        return SimpleNamespace(shape=(self.keys,), minimum=-1.0, maximum=1.0,
                               dtype="float64")

    def _goal(self, idx: int) -> dict[str, list[float]]:
        selected = self.targets[min(idx, len(self.targets) - 1)]
        return {"goal": [float(k in selected) for k in range(self.keys)]}

    def reset(self) -> _SyntheticTimeStep:
        self.count = 0
        return _SyntheticTimeStep(self._goal(0), None, None)

    def step(self, action: tuple[float, ...]) -> _SyntheticTimeStep:
        self.step_calls += 1
        pressed = {i for i, value in enumerate(action) if value >= 0.5}
        wanted = set(self.targets[self.count])
        true_positive = len(wanted & pressed)
        reward = 2 * true_positive / (len(wanted) + len(pressed)) if (wanted or pressed) else 1.0
        self.count += 1
        return _SyntheticTimeStep(self._goal(self.count), reward,
                                  0.0 if self.count == len(self.targets) else 1.0,
                                  self.count == len(self.targets))


def fixture_trial(seed: int, mode: str) -> dict[str, Any]:
    """Deterministic 16-step scripted-target vs frozen-zero benchmark; NO learning."""
    if mode not in ("scripted_target", "frozen_zero"):
        raise PianoBridgeError("invalid fixture mode")
    env = SyntheticPianoEnv(seed)
    bridge = PianoBridge(env, piano_keys=env.keys, origin="synthetic_public",
                         array_factory=tuple)
    initial = bridge.reset()
    frames = [initial]
    for wanted in env.targets:
        action = [1.0 if i in wanted and mode == "scripted_target" else 0.0
                  for i in range(env.keys)]
        frames.append(bridge.step(action))
    if not frames[-1]["terminal"] or len(frames) != len(env.targets) + 1:
        raise PianoBridgeError("incomplete synthetic episode")
    payload = json.dumps(frames, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return {
        "mode": mode, "seed": seed, "steps": len(env.targets),
        "mean_reward": round(sum(x["reward"] for x in frames[1:]) / len(env.targets), 8),
        "trace_sha256": hashlib.sha256(payload).hexdigest(),
    }


def compare_fixture(seed: int = 1337, held_out_seed: int = 7331) -> dict[str, Any]:
    if type(seed) is not int or type(held_out_seed) is not int or seed == held_out_seed:
        raise PianoBridgeError("separate integer seeds required")
    train = [fixture_trial(seed, mode) for mode in ("scripted_target", "frozen_zero")]
    holdout = [fixture_trial(held_out_seed, mode) for mode in ("scripted_target", "frozen_zero")]
    return {
        "schema": REPORT_SCHEMA, "origin": "synthetic_public_only",
        "learned_skill_measured": False, "physical_piano_measured": False,
        "train": train, "held_out": holdout,
    }


def main(argv=None) -> int:
    """Run the offline PUBLIC synthetic comparison; never launch a real backend."""
    import argparse
    parser = argparse.ArgumentParser(description="Offline synthetic piano benchmark, not learning")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--held-out-seed", type=int, default=7331)
    args = parser.parse_args(argv)
    try:
        result = compare_fixture(args.seed, args.held_out_seed)
    except PianoBridgeError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
