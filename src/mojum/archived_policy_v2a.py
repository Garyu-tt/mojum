"""Opt-in local-cell motor policy with an explicit replay-stable v2 contract.

Separate from legacy Explorer v1: its snapshot, intent and learning schemas
are pinned in each new Aquarium bootstrap. No implicit v1 policy migration.
Only measured synthetic blocked/unblocked motion is a learning signal.
"""
from __future__ import annotations

import copy
import re

from .local_cell_sense import (
    ACTIONS, DevelopmentError, MAX_EPISODE_TICKS, MAX_ZONES,
    Proprioception, STEP_METERS,
)

SCHEMA = "mojum.developmental.explorer.local-cell/2"
# Issue #116: this archived draft must never answer to the bare canonical
# ``local_cell_v2`` name. The persisted SCHEMA above is unchanged; only the
# public configuration spelling is disambiguated from draft B.
MODE = "local_cell_v2_a_archive"
CELL_METERS = STEP_METERS  # 4 cm local spatial bucket; explicit v2 identity.
_CELL = re.compile(r"(?:0|[1-9][0-9]{0,4}):(?:0|[1-9][0-9]{0,4})\Z", re.ASCII)
_DELTAS = {
    "rest": (0.0, 0.0), "x+": (STEP_METERS, 0.0),
    "y+": (0.0, STEP_METERS), "x-": (-STEP_METERS, 0.0),
    "y-": (0.0, -STEP_METERS),
}


class LocalCellExplorer:
    """Deterministic motor play with local measured collision memory.

    The controller sees only typed proprioception (own coordinates, revision,
    virtual tick and heartbeat). It is never passed walls, reward, pain,
    goals, real motors or a future observation. Frozen v1 code stays intact.
    """

    def __init__(self):
        self.last_tick = -1
        self.updates = 0
        self.records = {}

    @staticmethod
    def _zone(sense):
        # Full-coordinate rounding is intentionally part of the pinned v2
        # policy. Never substitute a new quantizer under the same schema.
        return f"{round(sense.x / CELL_METERS)}:{round(sense.y / CELL_METERS)}"

    def snapshot(self):
        return {
            "schema": SCHEMA, "last_tick": self.last_tick,
            "updates": self.updates, "records": copy.deepcopy(self.records),
        }

    @classmethod
    def from_snapshot(cls, state):
        if (
            type(state) is not dict
            or set(state) != {"schema", "last_tick", "updates", "records"}
            or state["schema"] != SCHEMA
            or type(state["last_tick"]) is not int
            or not -1 <= state["last_tick"] < MAX_EPISODE_TICKS
            or type(state["updates"]) is not int
            or state["updates"] != state["last_tick"] + 1
            or type(state["records"]) is not dict
            or len(state["records"]) > MAX_ZONES
        ):
            raise DevelopmentError("invalid v2 motor snapshot")
        total = 0
        for cell, actions in state["records"].items():
            if (
                type(cell) is not str or len(cell) > 11
                or _CELL.fullmatch(cell) is None
                or any(int(v) > 25_000 for v in cell.split(":"))
                or type(actions) is not dict or not actions
                or not set(actions) <= set(ACTIONS)
            ):
                raise DevelopmentError("invalid v2 local-cell key or actions")
            for action, counters in actions.items():
                if (
                    type(counters) is not list or len(counters) != 2
                    or any(type(v) is not int for v in counters)
                    or not 0 <= counters[1] <= counters[0] <= MAX_EPISODE_TICKS
                    or counters[0] < 1
                ):
                    raise DevelopmentError("invalid v2 motor outcome counters")
                if counters[1] > 1 or (action == "rest" and counters[1]):
                    raise DevelopmentError("impossible blocked motor history")
                total += counters[0]
        if total != state["updates"]:
            raise DevelopmentError("inconsistent v2 learned event counts")
        restored = cls()
        restored.last_tick = state["last_tick"]
        restored.updates = state["updates"]
        restored.records = copy.deepcopy(state["records"])
        return restored

    def decide(self, sense):
        if type(sense) is not Proprioception or sense.tick != self.last_tick + 1:
            raise DevelopmentError("stale or unexpected v2 proprioception tick")
        cell = self._zone(sense)
        known = self.records.get(cell, {})
        rotation = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        options = ACTIONS[rotation:] + ACTIONS[:rotation]
        action = next((name for name in options
                       if known.get(name, [0, 0])[1] == 0), "rest")
        dx, dy = _DELTAS[action]
        return {
            "action": action, "dx": dx, "dy": dy,
            "expected_revision": sense.revision, "tick": sense.tick,
            "zone": cell, "source": "bounded_local_cell_motor_play_v2",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise DevelopmentError("forged or stale v2 measured motor outcome")
        cell, action = intent["zone"], intent["action"]
        if cell not in self.records and len(self.records) >= MAX_ZONES:
            raise DevelopmentError("v2 local-cell memory budget exhausted")
        count = self.records.setdefault(cell, {}).setdefault(action, [0, 0])
        count[0] += 1
        count[1] += int(blocked)
        self.last_tick = sense.tick
        self.updates += 1
        return {
            "schema": SCHEMA, "action": action, "measured_blocked": blocked,
            "observed_outcomes": count[0], "blocked_outcomes": count[1],
            "total_updates": self.updates,
            "source": "measured_local_cell_outcome_not_reward",
        }
