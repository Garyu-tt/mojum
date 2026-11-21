"""Versioned, opt-in local-cell goal-free motor policy for synthetic Aquarium.

Reuses canonical Proprioception, action vocabulary and measured blocked
outcome. This policy is intentionally separate from legacy Explorer so
existing private journal replay is never silently reinterpreted.

No goal/reward, pain, distress, geometry oracle, neural brain, hardware
actuation or access to private storage.
"""
from __future__ import annotations

import copy
import math
import re

from .local_cell_sense import (
    ACTIONS, STEP_METERS, MAX_EPISODE_TICKS, Proprioception,
)

SCHEMA = "mojum.developmental.local-cell/2"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/2"
MAX_CELLS = 512
_KEY = re.compile(r"(?:0|[1-9][0-9]{0,4}):(?:0|[1-9][0-9]{0,4})\Z", re.ASCII)
_MOVES = {
    "rest": (0.0, 0.0), "x+": (STEP_METERS, 0.0),
    "y+": (0.0, STEP_METERS), "x-": (-STEP_METERS, 0.0),
    "y-": (0.0, -STEP_METERS),
}


class LocalCellError(ValueError):
    """Invalid local-cell learned evidence, revision, or causal transition."""


class CellExplorer:
    """Per-4cm-cell measured motor inhibition; local CPU-only research policy."""

    def __init__(self):
        self.last_tick = -1
        self.updates = 0
        self.records = {}

    @staticmethod
    def _zone(sense):
        if type(sense) is not Proprioception:
            raise LocalCellError("canonical proprioception required")
        # A deterministic quantization of *only the agent's own position*.
        return "%d:%d" % (
            round(sense.x / STEP_METERS),
            round(sense.y / STEP_METERS),
        )

    def snapshot(self):
        return {
            "schema": SCHEMA,
            "cell_size_m": STEP_METERS,
            "last_tick": self.last_tick,
            "updates": self.updates,
            "records": copy.deepcopy(self.records),
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        if (type(snapshot) is not dict
                or set(snapshot) != {"schema", "cell_size_m",
                                    "last_tick", "updates", "records"}
                or snapshot["schema"] != SCHEMA
                or type(snapshot["cell_size_m"]) is not float
                or snapshot["cell_size_m"] != STEP_METERS
                or type(snapshot["last_tick"]) is not int
                or not -1 <= snapshot["last_tick"] < MAX_EPISODE_TICKS
                or type(snapshot["updates"]) is not int
                or snapshot["updates"] != snapshot["last_tick"] + 1
                or type(snapshot["records"]) is not dict
                or len(snapshot["records"]) > MAX_CELLS):
            raise LocalCellError("invalid local-cell snapshot envelope")
        total = 0
        for zone, actions in snapshot["records"].items():
            if (type(zone) is not str or not _KEY.fullmatch(zone)
                    or any(int(v) > 25000 for v in zone.split(":"))
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise LocalCellError("invalid 4cm cell key or action set")
            for action, counters in actions.items():
                if (type(counters) is not list or len(counters) != 2
                        or any(type(x) is not int for x in counters)
                        or not 1 <= counters[0] <= MAX_EPISODE_TICKS
                        or not 0 <= counters[1] <= counters[0]):
                    raise LocalCellError("invalid measured evidence counters")
                if counters[1] > 1 or (action == "rest" and counters[1]):
                    raise LocalCellError("impossible blocked motor history")
                total += counters[0]
        if total != snapshot["updates"]:
            raise LocalCellError("inconsistent causal evidence totals")
        instance = cls()
        instance.last_tick = snapshot["last_tick"]
        instance.updates = snapshot["updates"]
        instance.records = copy.deepcopy(snapshot["records"])
        return instance

    def decide(self, sense):
        if type(sense) is not Proprioception or sense.tick != self.last_tick + 1:
            raise LocalCellError("stale or wrong proprioception tick")
        zone = self._zone(sense)
        evidence = self.records.get(zone, {})
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        rotation = ACTIONS[shift:] + ACTIONS[:shift]
        action = next(
            (choice for choice in rotation
             if evidence.get(choice, [0, 0])[1] == 0), "rest",
        )
        dx, dy = _MOVES[action]
        return {
            "action": action, "dx": dx, "dy": dy,
            "expected_revision": sense.revision,
            "tick": sense.tick, "zone": zone,
            "source": "bounded_goal_free_local_cell_motor_play_v2",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise LocalCellError("stale or fabricated measured motor outcome")
        zone, action = intent["zone"], intent["action"]
        if zone not in self.records and len(self.records) >= MAX_CELLS:
            raise LocalCellError("local cell evidence capacity exhausted")
        counters = self.records.setdefault(zone, {}).setdefault(action, [0, 0])
        counters[0] += 1
        counters[1] += int(blocked)
        self.last_tick = sense.tick
        self.updates += 1
        return {
            "schema": LEARNING_SCHEMA,
            "action": action,
            "measured_blocked": blocked,
            "observed_outcomes": counters[0],
            "blocked_outcomes": counters[1],
            "total_updates": self.updates,
            "source": "observed_point_room_result_not_reward",
        }
