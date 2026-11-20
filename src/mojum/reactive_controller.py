"""Deterministic, non-learning CPU fixture policy for point-room-only movement.

The output is *never* a hardware command or a MuJoCo joint target. It is a
bounded synthetic displacement for the existing point-room fixture. The policy
uses only the current state, current synthetic interoception, and last tick's
committed movement feedback; it has no future-world access.
"""

import math

from .world_room import Room, RoomState, initial_state


SCHEMA = "mojum.controller.reactive-point-room/1"


class ControllerError(ValueError):
    """Malformed sensory feedback, goal or untrusted fixture state."""


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def checked_goal(room, goal):
    if (not isinstance(room, Room) or not isinstance(goal, (list, tuple))
            or len(goal) != 2 or not all(_number(v) for v in goal)):
        raise ControllerError("invalid fixture goal")
    normalized = (float(goal[0]), float(goal[1]))
    try:
        initial_state(room, *normalized)  # Reject off-room or obstacle goals.
    except (ValueError, TypeError, OverflowError) as exc:
        raise ControllerError("goal is not reachable as a point fixture position") from exc
    return normalized


def decide(room, state, sensory, goal, previous=None):
    """Issue one <=4 cm world-only displacement, reacting to previous blockage.

    Heuristic: try the goal's X direction, then Y; if the prior X attempt
    was blocked, use the Y direction for this tick. This is NOT pathfinding,
    learned behavior, visual perception, or guaranteed obstacle avoidance.
    """
    target = checked_goal(room, goal)
    if not isinstance(state, RoomState):
        raise ControllerError("RoomState required")
    if not isinstance(sensory, dict) or sensory.get("kind") != "interoception":
        raise ControllerError("current interoception required")
    heartbeat = sensory.get("heartbeat")
    at = sensory.get("observed_at_ns")
    if (not isinstance(heartbeat, dict) or type(heartbeat.get("pulse")) is not bool
            or type(at) is not int or at < 0):
        raise ControllerError("invalid synthetic heartbeat")
    if previous is not None:
        if (not isinstance(previous, dict) or set(previous) != {"dx", "dy", "blocked"}
                or type(previous["blocked"]) is not bool
                or not _number(previous["dx"]) or not _number(previous["dy"])):
            raise ControllerError("invalid previous committed movement feedback")
    if not _number(state.x) or not _number(state.y):
        raise ControllerError("non-finite world state")
    speed = 0.02 if heartbeat["pulse"] else 0.04
    gap_x, gap_y = target[0] - state.x, target[1] - state.y
    x_needed = abs(gap_x) > 1e-10
    y_needed = abs(gap_y) > 1e-10
    axis = "x" if x_needed else ("y" if y_needed else "idle")
    if (axis == "x" and y_needed and previous is not None
            and previous["blocked"] and abs(previous["dx"]) > 0
            and previous["dy"] == 0):
        axis = "y"
    dx = math.copysign(min(speed, abs(gap_x)), gap_x) if axis == "x" else 0.0
    dy = math.copysign(min(speed, abs(gap_y)), gap_y) if axis == "y" else 0.0
    return {
        "schema": SCHEMA,
        "status": "DETERMINISTIC_NONLEARNING_FIXTURE",
        "world_revision": state.revision,
        "observed_at_ns": at,
        "goal": list(target),
        "axis": axis,
        "prior_blocked": previous["blocked"] if previous else None,
        "dx": dx, "dy": dy,
        "motor_command": None,
        "actuation": "SYNTHETIC_POINT_ROOM_ONLY",
    }
