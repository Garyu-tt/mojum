"""Small CPU-only contextual-bandit policy for the synthetic Mojum point room.

This is actual online reward learning, but NOT a neural brain, path planner,
MuJoCo controller, or physical actuator. A proposal is data, never an
executable motor command. The aquarium commits its updated policy only after
the private Noosphere event append succeeds.
"""

import copy
import hashlib
import json
import math
import re

from .reactive_controller import ControllerError, checked_goal
from .world_room import Room, RoomState


SCHEMA = "mojum.policy.tabular-axis/1"
ACTIONS = ("x", "y")
MAX_CELLS = 4096
SEED = 1979
_CELL = re.compile(r"(?:0|[1-9][0-9]{0,6}):(?:0|[1-9][0-9]{0,6})\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class PolicyError(ValueError):
    """Fail-closed invalid policy state, proposal or world feedback."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cell(state):
    if not isinstance(state, RoomState) or not _finite(state.x) or not _finite(state.y):
        raise PolicyError("invalid point-room state")
    x, y = state.x * 2, state.y * 2
    if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < 10_000_000 and 0 <= y < 10_000_000):
        raise PolicyError("point-room exceeds policy cell budget")
    return f"{math.floor(x)}:{math.floor(y)}"


def _sensory(sensory):
    if not isinstance(sensory, dict) or sensory.get("kind") != "interoception":
        raise PolicyError("synthetic interoception required")
    h = sensory.get("heartbeat")
    timestamp = sensory.get("observed_at_ns")
    if (not isinstance(h, dict) or type(h.get("pulse")) is not bool
            or type(timestamp) is not int or timestamp < 0):
        raise PolicyError("invalid sensory frame")
    return h["pulse"], timestamp


def gate_fixture_proposal(room, state, sensory, goal, proposal):
    """Strict independent world-only boundary; never sends hardware commands."""
    target = checked_goal(room, goal)
    pulse, timestamp = _sensory(sensory)
    if not isinstance(proposal, dict) or set(proposal) != {
        "schema", "status", "world_revision", "observed_at_ns",
        "goal", "cell", "axis", "dx", "dy", "exploration",
        "model_updates", "model_sha256", "motor_command", "actuation",
    }:
        raise PolicyError("invalid policy proposal envelope")
    if (proposal["schema"] != SCHEMA or proposal["status"] != "TABULAR_ONLINE_REWARD_LEARNING"
            or proposal["world_revision"] != state.revision
            or proposal["observed_at_ns"] != timestamp
            or proposal["goal"] != list(target)
            or proposal["cell"] != _cell(state)
            or type(proposal["exploration"]) is not bool
            or type(proposal["model_updates"]) is not int
            or proposal["model_updates"] < 0
            or not isinstance(proposal["model_sha256"], str)
            or not _SHA.fullmatch(proposal["model_sha256"])
            or proposal["motor_command"] is not None
            or proposal["actuation"] != "SYNTHETIC_POINT_ROOM_ONLY"):
        raise PolicyError("untrusted fixture proposal metadata")
    dx, dy = proposal["dx"], proposal["dy"]
    if not _finite(dx) or not _finite(dy):
        raise PolicyError("nonfinite fixture proposal")
    speed = 0.02 if pulse else 0.04
    gap_x, gap_y = target[0] - state.x, target[1] - state.y
    axis = proposal["axis"]
    if axis == "idle":
        if dx != 0 or dy != 0 or abs(gap_x) > 1e-10 or abs(gap_y) > 1e-10:
            raise PolicyError("invalid idle proposal")
    elif axis == "x":
        expected = math.copysign(min(speed, abs(gap_x)), gap_x)
        if abs(gap_x) <= 1e-10 or dx != expected or dy != 0:
            raise PolicyError("invalid bounded x-axis proposal")
    elif axis == "y":
        expected = math.copysign(min(speed, abs(gap_y)), gap_y)
        if abs(gap_y) <= 1e-10 or dy != expected or dx != 0:
            raise PolicyError("invalid bounded y-axis proposal")
    else:
        raise PolicyError("unknown axis")
    return float(dx), float(dy)


class TabularAxisPolicy:
    """Two-armed contextual bandit, discretized by 0.5 m world position.

    Values start at zero, then update by 0.25 * (observed reward - value).
    Reward uses only subsequent world feedback: blocked=-1; otherwise
    normalized Manhattan progress toward the pinned point-room goal.
    Exploration is a deterministic fixed-seed LCG for exact replay, NOT
    cryptographic randomness or independent intelligent exploration.
    """

    def __init__(self):
        self._table = {}
        self._rng = SEED
        self._updates = 0

    @property
    def updates(self):
        return self._updates

    def snapshot(self):
        return {"schema": SCHEMA, "rng_state": self._rng,
                "updates": self._updates, "table": copy.deepcopy(self._table)}

    @classmethod
    def from_snapshot(cls, state):
        if not isinstance(state, dict) or set(state) != {"schema", "rng_state", "updates", "table"}:
            raise PolicyError("incompatible policy snapshot")
        if state["schema"] != SCHEMA:
            raise PolicyError("unknown policy version")
        if (type(state["rng_state"]) is not int or not 0 <= state["rng_state"] < 2 ** 32
                or type(state["updates"]) is not int or not 0 <= state["updates"] <= 10_000_000
                or not isinstance(state["table"], dict) or len(state["table"]) > MAX_CELLS):
            raise PolicyError("invalid policy state")
        counted = 0
        for key, entry in state["table"].items():
            if not isinstance(key, str) or not _CELL.fullmatch(key):
                raise PolicyError("invalid spatial cell")
            if not isinstance(entry, dict) or set(entry) != {"values", "visits"}:
                raise PolicyError("invalid policy cell")
            values, visits = entry["values"], entry["visits"]
            if (not isinstance(values, list) or len(values) != 2
                    or not all(_finite(v) and -1 <= v <= 1 for v in values)
                    or not isinstance(visits, list) or len(visits) != 2
                    or not all(type(n) is int and 0 <= n <= state["updates"] for n in visits)):
                raise PolicyError("invalid Q value or visit count")
            counted += sum(visits)
        if counted != state["updates"]:
            raise PolicyError("inconsistent learned visit total")
        inst = cls()
        inst._rng, inst._updates = state["rng_state"], state["updates"]
        inst._table = copy.deepcopy(state["table"])
        return inst

    def _random(self):
        self._rng = (1664525 * self._rng + 1013904223) & 0xffffffff
        return self._rng

    def propose(self, room, state, sensory, goal):
        target = checked_goal(room, goal)
        pulse, timestamp = _sensory(sensory)
        cell = _cell(state)
        available = []
        if abs(target[0] - state.x) > 1e-10:
            available.append("x")
        if abs(target[1] - state.y) > 1e-10:
            available.append("y")
        axis, exploring = "idle", False
        if available:
            values = self._table.get(cell, {"values": [0.0, 0.0]})["values"]
            axis = max(available, key=lambda a: (values[ACTIONS.index(a)], -ACTIONS.index(a)))
            exploring = self._random() / 2 ** 32 < 0.125
            if exploring:
                axis = available[self._random() % len(available)]
        step = 0.02 if pulse else 0.04
        dx = math.copysign(min(step, abs(target[0] - state.x)), target[0] - state.x) if axis == "x" else 0.0
        dy = math.copysign(min(step, abs(target[1] - state.y)), target[1] - state.y) if axis == "y" else 0.0
        proposal = {
            "schema": SCHEMA, "status": "TABULAR_ONLINE_REWARD_LEARNING",
            "world_revision": state.revision, "observed_at_ns": timestamp,
            "goal": list(target), "cell": cell, "axis": axis,
            "dx": dx, "dy": dy, "exploration": exploring,
            "model_updates": self._updates, "model_sha256": _hash(self.snapshot()),
            "motor_command": None, "actuation": "SYNTHETIC_POINT_ROOM_ONLY",
        }
        gate_fixture_proposal(room, state, sensory, target, proposal)
        return proposal

    def learn(self, room, before, after, observation, goal, proposal):
        target = checked_goal(room, goal)
        if (not isinstance(before, RoomState) or not isinstance(after, RoomState)
                or not isinstance(observation, dict)
                or observation.get("revision") != after.revision
                or observation.get("position") != [after.x, after.y]
                or observation.get("tick") != before.tick + 1
                or after.tick != before.tick + 1
                or type(observation.get("blocked")) is not bool
                or not isinstance(proposal, dict)
                or proposal.get("cell") != _cell(before)
                or proposal.get("model_updates") != self._updates):
            raise PolicyError("inconsistent world feedback")
        axis = proposal.get("axis")
        if axis not in ("idle", *ACTIONS):
            raise PolicyError("unknown trained action")
        if axis == "idle":
            if before.x != after.x or before.y != after.y or observation["blocked"]:
                raise PolicyError("idle action changed the world")
            return {"trained": False, "reward": None, "updates": self._updates,
                    "policy_sha256": _hash(self.snapshot())}
        dx, dy = proposal["dx"], proposal["dy"]
        if not _finite(dx) or not _finite(dy):
            raise PolicyError("invalid action feedback")
        if observation["blocked"]:
            if after.x != before.x or after.y != before.y:
                raise PolicyError("blocked move changed the world")
            reward = -1.0
        else:
            if after.x != before.x + dx or after.y != before.y + dy:
                raise PolicyError("world movement does not match proposal")
            old_distance = abs(target[0] - before.x) + abs(target[1] - before.y)
            new_distance = abs(target[0] - after.x) + abs(target[1] - after.y)
            reward = max(-1.0, min(1.0, (old_distance - new_distance) / max(abs(dx), abs(dy))))
        key = _cell(before)
        if key not in self._table:
            if len(self._table) >= MAX_CELLS:
                raise PolicyError("policy state budget exceeded")
            self._table[key] = {"values": [0.0, 0.0], "visits": [0, 0]}
        entry = self._table[key]
        i = ACTIONS.index(axis)
        entry["values"][i] = max(-1.0, min(1.0, entry["values"][i] + 0.25 * (reward - entry["values"][i])))
        entry["visits"][i] += 1
        self._updates += 1
        return {"trained": True, "reward": reward, "updates": self._updates,
                "cell": key, "action": axis, "q_after": entry["values"][i],
                "policy_sha256": _hash(self.snapshot())}
