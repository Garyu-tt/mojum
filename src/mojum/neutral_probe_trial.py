"""Opt-in, bounded neutral-probe experimental control for Mojum.

Uses the canonical deterministic room, typed proprioception and Explorer;
does NOT start a second organism, neural substrate, SQLite journal, or device.
One arm updates from *measured* blocked movements; the frozen control
repeats the same innate pulse-paced action rotation without adapting.

No explicit valence, pain, aversion, reward or homeostatic distress channel is
implemented here. This does NOT prove absence of experience or suffering.
"""
from __future__ import annotations

import argparse
import json
import math

from .developmental_policy import ACTIONS, Explorer, Proprioception, STEP_METERS
from .world_room import Room, advance, initial_state

SCHEMA = "mojum.research.neutral-probe-trial/1"
COMPARISON_SCHEMA = "mojum.research.neutral-probe-comparison/1"
MAX_TICKS = 512
MODES = ("adaptive", "frozen", "local_cell")
_DELTAS = {
    "rest": (0.0, 0.0),
    "x+": (STEP_METERS, 0.0),
    "x-": (-STEP_METERS, 0.0),
    "y+": (0.0, STEP_METERS),
    "y-": (0.0, -STEP_METERS),
}


class NeutralProbeError(ValueError):
    """Invalid research fixture; no partial experiment is performed."""


class LocalCellPlay:
    """Research-only positional control: local blocked evidence, no reward.

    Unlike the canonical Explorer's half-meter zone, evidence is tied to
    a single quantized 4 cm own-position cell. The controller has no room
    geometry, future observations, collision oracle or direct actuator.
    It can recover from a blocked direction after changing cells.
    """

    def __init__(self):
        self.updates = 0
        self.records = {}

    @staticmethod
    def _cell(sense):
        return "%d:%d" % (
            round(sense.x / STEP_METERS),
            round(sense.y / STEP_METERS),
        )

    def decide(self, sense):
        if type(sense) is not Proprioception or sense.tick != self.updates:
            raise NeutralProbeError("stale or invalid local-cell sensation")
        cell = self._cell(sense)
        history = self.records.get(cell, {})
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        candidates = ACTIONS[shift:] + ACTIONS[:shift]
        action = next(
            (name for name in candidates
             if history.get(name, [0, 0])[1] == 0),
            "rest",
        )
        dx, dy = _DELTAS[action]
        return {"action": action, "dx": dx, "dy": dy,
                "cell": cell, "tick": sense.tick,
                "expected_revision": sense.revision}

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise NeutralProbeError("local-cell outcome must be measured")
        cell, action = intent["cell"], intent["action"]
        if cell not in self.records and len(self.records) >= MAX_TICKS:
            raise NeutralProbeError("research cell capacity exhausted")
        counts = self.records.setdefault(cell, {}).setdefault(action, [0, 0])
        counts[0] += 1
        counts[1] += int(blocked)
        self.updates += 1

    def snapshot(self):
        return {
            "schema": "mojum.research.local-cell-play/1",
            "updates": self.updates,
            "cells": len(self.records),
            "records": {
                cell: {action: list(counts) for action, counts in acts.items()}
                for cell, acts in self.records.items()
            },
        }


def _validated(room, start, pulses, mode):
    if type(room) is not Room:
        raise NeutralProbeError("canonical Room fixture required")
    if mode not in MODES or type(mode) is not str:
        raise NeutralProbeError("mode must be adaptive, frozen or local_cell")
    if (type(start) not in (tuple, list) or len(start) != 2
            or any(type(v) not in (int, float) for v in start)):
        raise NeutralProbeError("start must be two finite numeric coordinates")
    try:
        position = tuple(float(v) for v in start)
    except (OverflowError, ValueError) as exc:
        raise NeutralProbeError("invalid start coordinates") from exc
    if not all(math.isfinite(v) for v in position):
        raise NeutralProbeError("nonfinite start coordinates")
    if (type(pulses) not in (tuple, list) or not 1 <= len(pulses) <= MAX_TICKS
            or any(type(v) is not bool for v in pulses)):
        raise NeutralProbeError("require 1..512 explicit boolean pulse observations")
    return position


def trial(*, room, start, pulses, mode):
    """Run an isolated point-room experiment; returns deterministic trace.

    The policy sees only current own position, world revision, tick and pulse.
    Room dimensions/obstacles are used ONLY by the independent world advance.
    Frozen control follows the same rotating action sequence without learning.
    No SQLite, network, hardware capability or private Noosphere is accessed.
    """
    position = _validated(room, start, pulses, mode)
    state = initial_state(room, *position)
    learner = (Explorer() if mode == "adaptive" else
               LocalCellPlay() if mode == "local_cell" else None)
    trace = []
    blocked_count = 0
    for tick, pulse in enumerate(pulses):
        observation = Proprioception(
            tick, state.revision, state.x, state.y, pulse
        )
        if learner is None:
            action = ACTIONS[(tick + int(pulse)) % len(ACTIONS)]
            dx, dy = _DELTAS[action]
            intent = None
        else:
            intent = learner.decide(observation)
            action = intent["action"]
            dx, dy = intent["dx"], intent["dy"]
        next_state, outcome = advance(
            room, state, tick=tick + 1, dx=dx, dy=dy,
            expected_revision=state.revision,
        )
        if learner is not None:
            # The only training signal is the *measured* physical fixture
            # result. No desired endpoint, world map or oracle is supplied.
            learner.observe(observation, intent, outcome["blocked"])
        blocked_count += int(outcome["blocked"])
        trace.append({
            "tick": tick,
            "heartbeat_pulse": pulse,
            "action": action,
            "requested_delta_xy": [dx, dy],
            "blocked": outcome["blocked"],
            "position_xy": list(outcome["position"]),
        })
        state = next_state
    # A learner which suppresses every possible action can look 'good'
    # on blocked counts while becoming immobile. Measure that failure mode
    # explicitly; these are diagnostics, never rewards or survival goals.
    rest_count = sum(frame["action"] == "rest" for frame in trace)
    attempted_count = len(trace) - rest_count
    successful_count = sum(
        frame["action"] != "rest" and not frame["blocked"] for frame in trace
    )
    longest_rest, running_rest = 0, 0
    for frame in trace:
        running_rest = running_rest + 1 if frame["action"] == "rest" else 0
        longest_rest = max(longest_rest, running_rest)
    # Only a display-scale coverage diagnostic; this is NOT a world map
    # supplied to either controller.
    unique_positions = len({
        tuple(round(v, 6) for v in frame["position_xy"]) for frame in trace
    })
    stalled = (len(pulses) >= 32 and
               longest_rest >= max(8, len(pulses) // 4))
    return {
        "schema": SCHEMA,
        "mode": mode,
        "classification": "SYNTHETIC_POINT_ROOM_RESEARCH_ONLY",
        "ticks": len(pulses),
        "blocked_count": blocked_count,
        "rest_count": rest_count,
        "attempted_movement_count": attempted_count,
        "successful_movement_count": successful_count,
        "unique_positions_1um": unique_positions,
        "longest_rest_streak": longest_rest,
        "stalled_warning": stalled,
        "final_position_xy": [state.x, state.y],
        "trace": trace,
        "learned_state": learner.snapshot() if learner is not None else None,
    }


def compare(*, room, start, pulses):
    """Pair arms with identical initial conditions and pulse observations."""
    _validated(room, start, pulses, "adaptive")
    adaptive = trial(room=room, start=start, pulses=pulses, mode="adaptive")
    frozen = trial(room=room, start=start, pulses=pulses, mode="frozen")
    local_cell = trial(room=room, start=start, pulses=pulses,
                       mode="local_cell")
    return {
        "schema": COMPARISON_SCHEMA,
        "classification": "BOUNDED_SYNTHETIC_NEGATIVE_CONTROL",
        "adaptive": adaptive,
        "frozen": frozen,
        "local_cell": local_cell,
        "blocked_difference_frozen_minus_adaptive":
            frozen["blocked_count"] - adaptive["blocked_count"],
        "interpretation": (
            "COARSE_ZONE_STALLED_LOCAL_CELL_MOBILE"
            if adaptive["stalled_warning"] and not local_cell["stalled_warning"]
            else "STALLED_ADAPTIVE_NOT_SUCCESS"
            if adaptive["stalled_warning"]
            else "MEASURED_FIXTURE_DIFFERENCE_ONLY"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mojum synthetic neutral-probe vs frozen control (no DB)"
    )
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--mode", choices=("compare", *MODES), default="compare")
    args = parser.parse_args(argv)
    if not 1 <= args.ticks <= MAX_TICKS:
        parser.error("ticks must be in 1..512")
    # Tiny corner-room exposes repeatable contacts and provides a meaningful
    # trial without external objects, privileged controls or user data.
    room = Room(0.16, 0.16)
    pulses = tuple(i % 2 == 0 for i in range(args.ticks))
    kwargs = {"room": room, "start": (0.16, 0.16), "pulses": pulses}
    result = compare(**kwargs) if args.mode == "compare" else trial(
        **kwargs, mode=args.mode
    )
    # CLI shows bounded aggregate counts only. Detailed deterministic trace
    # is available through the Python API, not printed to terminal by default.
    if args.mode == "compare":
        summary = {
            "schema": COMPARISON_SCHEMA,
            "ticks": args.ticks,
            "adaptive_blocked": result["adaptive"]["blocked_count"],
            "frozen_blocked": result["frozen"]["blocked_count"],
            "local_cell_blocked": result["local_cell"]["blocked_count"],
            "local_cell_rest": result["local_cell"]["rest_count"],
            "local_cell_stalled_warning": result["local_cell"]["stalled_warning"],
            "blocked_difference": result["blocked_difference_frozen_minus_adaptive"],
            "adaptive_rest": result["adaptive"]["rest_count"],
            "frozen_rest": result["frozen"]["rest_count"],
            "adaptive_stalled_warning": result["adaptive"]["stalled_warning"],
            "interpretation": result["interpretation"],
            "classification": result["classification"],
        }
    else:
        summary = {
            "schema": SCHEMA, "ticks": args.ticks, "mode": args.mode,
            "blocked_count": result["blocked_count"],
            "rest_count": result["rest_count"],
            "stalled_warning": result["stalled_warning"],
            "classification": result["classification"],
        }
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    return summary


if __name__ == "__main__":
    main()
