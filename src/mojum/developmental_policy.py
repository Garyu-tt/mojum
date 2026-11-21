"""Bounded goal-free exploratory policy for a synthetic point-room.

Learns from numeric self-position, heartbeat and actual blocked/unblocked
movement outcomes only. No geometry/oracle, privileged state, reward goal,
physical actuation or neural-brain claim.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass

SCHEMA = "mojum.developmental.explorer/1"
SENSE_SCHEMA = "mojum.developmental.proprioception/1"
ACTIONS = ("rest", "x+", "y+", "x-", "y-")
STEP_METERS = 0.04
MAX_EPISODE_TICKS = 9999
MAX_ZONES = 512
_ZONE = re.compile(r"(?:0|[1-9][0-9]{0,3}):(?:0|[1-9][0-9]{0,3})\Z", re.ASCII)
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class DevelopmentError(ValueError):
    """Invalid synthetic observation, model or causal transition."""


def _finite(x):
    if type(x) not in (int, float):
        return False
    try:
        return math.isfinite(x)
    except (OverflowError, ValueError, TypeError):
        # Python raises OverflowError when converting unbounded ints to float.
        return False


@dataclass(frozen=True)
class Proprioception:
    tick: int
    revision: str
    x: float
    y: float
    pulse: bool

    def __post_init__(self):
        if (type(self.tick) is not int or not 0 <= self.tick < MAX_EPISODE_TICKS
                or type(self.revision) is not str
                or _REVISION.fullmatch(self.revision) is None
                or not _finite(self.x) or not _finite(self.y)
                or not 0 <= self.x <= 1000 or not 0 <= self.y <= 1000
                or type(self.pulse) is not bool):
            raise DevelopmentError("invalid numeric proprioception")

    def public(self):
        return {"schema": SENSE_SCHEMA, "tick": self.tick,
                "world_revision": self.revision,
                "position_xy": [float(self.x), float(self.y)],
                "heartbeat_pulse": self.pulse,
                "source": "synthetic_proprioception_not_geometry"}


class Explorer:
    """Deterministic play; inhibit actions causing measured blocked movement."""

    def __init__(self):
        self.last_tick = -1
        self.updates = 0
        self.records = {}

    def snapshot(self):
        return {"schema": SCHEMA, "last_tick": self.last_tick,
                "updates": self.updates, "records": copy.deepcopy(self.records)}

    @classmethod
    def from_snapshot(cls, state):
        if (type(state) is not dict or set(state) !=
                {"schema", "last_tick", "updates", "records"}
                or state["schema"] != SCHEMA
                or type(state["last_tick"]) is not int
                or not -1 <= state["last_tick"] < MAX_EPISODE_TICKS
                or type(state["updates"]) is not int
                or state["updates"] != state["last_tick"] + 1
                or type(state["records"]) is not dict
                or len(state["records"]) > MAX_ZONES):
            raise DevelopmentError("invalid developmental snapshot")
        total = 0
        for zone, actions in state["records"].items():
            if (type(zone) is not str or _ZONE.fullmatch(zone) is None
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise DevelopmentError("invalid learned state key")
            for counters in actions.values():
                if (type(counters) is not list or len(counters) != 2
                        or any(type(x) is not int for x in counters)
                        or not 0 <= counters[1] <= counters[0] <= MAX_EPISODE_TICKS):
                    raise DevelopmentError("invalid measured outcome counters")
                total += counters[0]
        if total != state["updates"]:
            raise DevelopmentError("inconsistent learned evidence totals")
        result = cls()
        result.last_tick = state["last_tick"]
        result.updates = state["updates"]
        result.records = copy.deepcopy(state["records"])
        return result

    @staticmethod
    def _zone(sense):
        return f"{int(sense.x * 2)}:{int(sense.y * 2)}"

    def decide(self, sense, *, avoid=(), prefer=()):
        if type(sense) is not Proprioception or sense.tick != self.last_tick + 1:
            raise DevelopmentError("stale or unexpected proprioception tick")
        for suggestions in (avoid, prefer):
            if (type(suggestions) is not tuple
                    or any(type(a) is not str or a not in ACTIONS
                           for a in suggestions)
                    or len(suggestions) != len(set(suggestions))):
                raise DevelopmentError("invalid learned motor affordances")
        if "rest" in avoid:
            raise DevelopmentError("cannot veto synthetic safe rest")
        zone = self._zone(sense)
        stored = self.records.get(zone, {})
        # Original rotating motor play is the fallback. The optional guidance
        # comes solely from prior measured sensor/action outcomes; no obstacle
        # geometry, future state or privileged toy metadata is provided.
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        choices = ACTIONS[shift:] + ACTIONS[:shift]
        permitted = [a for a in choices
                     if a not in avoid and stored.get(a, [0, 0])[1] == 0]
        preferred = [a for a in permitted if a in prefer]
        action = (preferred or permitted or ["rest"])[0]
        delta = {"rest": (0., 0.), "x+": (STEP_METERS, 0.),
                 "x-": (-STEP_METERS, 0.), "y+": (0., STEP_METERS),
                 "y-": (0., -STEP_METERS)}
        dx, dy = delta[action]
        return {"action": action, "dx": dx, "dy": dy,
                "expected_revision": sense.revision, "tick": sense.tick,
                "zone": zone, "source": "bounded_goal_free_motor_play"}

    def observe(self, sense, intent, blocked, *, avoid=(), prefer=()):
        if type(blocked) is not bool or intent != self.decide(
                sense, avoid=avoid, prefer=prefer):
            raise DevelopmentError("forged or stale measured motor outcome")
        zone, action = intent["zone"], intent["action"]
        if zone not in self.records and len(self.records) >= MAX_ZONES:
            raise DevelopmentError("developmental evidence capacity exhausted")
        counters = self.records.setdefault(zone, {}).setdefault(action, [0, 0])
        counters[0] += 1
        counters[1] += int(blocked)
        self.last_tick = sense.tick
        self.updates += 1
        return {"schema": SCHEMA, "action": action,
                "measured_blocked": blocked,
                "observed_outcomes": counters[0],
                "blocked_outcomes": counters[1],
                "total_updates": self.updates,
                "source": "observed_point_room_result_not_reward"}
