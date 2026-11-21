"""Evaluation-only harness for the isolated ``motor_play_v2`` profile.

This module runs the bounded-attempt profile through the deterministic
point-room fixture and reports the metrics issue #106 requires: attempts,
blocked outcomes, successful movement, approximate coverage, longest rest
streak and idle fraction.

It is **not** installed in the Aquarium, does not write to any journal and does
not alter the v1 wire schema. Rooms, starts and pulse-free tick counts are
fixed here so results are reproducible across CPython 3.11/3.12/3.13.
"""

from __future__ import annotations

import argparse
import hashlib
import json

from .motor_play_v2 import (
    MAX_CONSECUTIVE_REST,
    MAX_OBSERVATIONS,
    MOVES,
    PROFILE,
    SCHEMA as POLICY_SCHEMA,
    MotorPlayV2,
)
from .world_room import Room, advance, initial_state

SCHEMA = "mojum.eval.motor-play-v2/1"
ZONE_M = 0.5

#: Named fixtures. ``blocked_all`` is intentionally tiny: every cardinal step
#: leaves the room, which is the worst case for learned inhibition.
ROOMS = {
    "canonical": (Room(4, 4, ((2, 1, 0.1, 2),)), (1.0, 2.0)),
    "open": (Room(4, 4), (2.0, 1.0)),
    "corner": (Room(0.5, 0.5), (0.0625, 0.0625)),
    "blocked_all": (Room(0.2, 0.2), (0.1, 0.1)),
    "held_out_maze": (Room(3, 5, ((1, 1, 0.4, 0.4), (2, 3, 0.5, 0.5))), (0.5, 4.5)),
}


class EvalError(ValueError):
    """Unsupported evaluation request."""


def _zone(x, y):
    # Coarse 0.5 m zone, the same granularity issue #106 criticises; coverage
    # is reported separately from the policy so it cannot influence learning.
    return (int(x // ZONE_M), int(y // ZONE_M))


def run_profile(room, start, ticks, *, policy=None):
    """Drive ``policy`` through ``room`` and return measured, reproducible metrics."""
    if not isinstance(room, Room):
        raise EvalError("a Room fixture is required")
    if type(ticks) is not int or not 0 <= ticks <= MAX_OBSERVATIONS:
        raise EvalError(f"ticks must be an int in [0, {MAX_OBSERVATIONS}]")
    agent = MotorPlayV2() if policy is None else policy
    state = initial_state(room, *start)
    attempts = {name: 0 for name, _, _ in (*MOVES, ("rest", 0.0, 0.0))}
    blocked_by_action = {name: 0 for name in attempts}
    trace = []
    positions = {(state.x, state.y)}
    zones = {_zone(state.x, state.y)}
    longest_rest, running_rest = 0, 0
    successes = 0
    for tick in range(ticks):
        intent = agent.choose(tick)
        next_state, outcome = advance(
            room, state, tick=state.tick + 1, dx=intent["dx"], dy=intent["dy"],
            expected_revision=state.revision,
        )
        agent.observe(tick=tick, kind=intent["kind"], blocked=outcome["blocked"])
        attempts[intent["kind"]] += 1
        if outcome["blocked"]:
            blocked_by_action[intent["kind"]] += 1
        elif intent["kind"] != "rest":
            successes += 1
        running_rest = running_rest + 1 if intent["kind"] == "rest" else 0
        longest_rest = max(longest_rest, running_rest)
        trace.append(intent["kind"])
        positions.add((next_state.x, next_state.y))
        zones.add(_zone(next_state.x, next_state.y))
        state = next_state
    trace_digest = hashlib.sha256(",".join(trace).encode("ascii")).hexdigest()
    rest_attempts = attempts["rest"]
    return {
        "schema": SCHEMA,
        "policy_schema": POLICY_SCHEMA,
        "profile": PROFILE,
        "ticks": ticks,
        "attempts": attempts,
        "blocked_by_action": blocked_by_action,
        "blocked_total": sum(blocked_by_action.values()),
        "successful_moves": successes,
        "rest_attempts": rest_attempts,
        "idle_fraction": (rest_attempts / ticks) if ticks else 0.0,
        "longest_rest_streak": longest_rest,
        "measured_rest_cap": MAX_CONSECUTIVE_REST,
        "distinct_positions": len(positions),
        "distinct_zones_50cm": len(zones),
        "final_position": [state.x, state.y],
        "action_trace_sha256": trace_digest,
        "decision_model_sha256": hashlib.sha256(
            json.dumps(agent.snapshot(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "physical_contact": False,
        "interface": "evaluation_only_not_aquarium_abi",
    }


def evaluate_named(name, ticks):
    if name not in ROOMS:
        raise EvalError(f"unknown fixture {name!r}; choose from {sorted(ROOMS)}")
    room, start = ROOMS[name]
    return {"fixture": name, **run_profile(room, start, ticks)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mojum-motor-play-v2",
        description="Evaluate the isolated bounded-rest motor-play v2 profile",
    )
    parser.add_argument("--fixture", action="append", choices=sorted(ROOMS),
                        help="fixture name (repeatable); default: all")
    parser.add_argument("--ticks", type=int, default=240)
    args = parser.parse_args(argv)
    names = args.fixture or sorted(ROOMS)
    try:
        report = {
            "schema": SCHEMA,
            "results": [evaluate_named(name, args.ticks) for name in names],
        }
    except (EvalError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
