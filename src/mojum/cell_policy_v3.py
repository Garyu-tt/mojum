"""R0.14 optional v3 local-cell policy: bounded deterministic retention.

Never overwrite/reinterpret legacy Explorer/1 or Local Cell/2 records.
No reward, pain, valence, route target, obstacle oracle or physical motor
authority. Only own proprioception/pulse and *measured* blocked outcomes.

Eviction is a data-retention budget, NOT a negative experiential signal.
"""
from __future__ import annotations

import copy
import re

from .developmental_policy import (
    ACTIONS, MAX_EPISODE_TICKS, Proprioception, STEP_METERS,
)

SCHEMA = "mojum.developmental.local-cell/3"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/3"
MAX_CELLS = 512
_ZONE = re.compile(
    r"(?:0|[1-9][0-9]{0,4}):(?:0|[1-9][0-9]{0,4})\Z", re.ASCII
)
_MOVES = {
    "rest": (0.0, 0.0),
    "x+": (STEP_METERS, 0.0),
    "y+": (0.0, STEP_METERS),
    "x-": (-STEP_METERS, 0.0),
    "y-": (0.0, -STEP_METERS),
}


class CellV3Error(ValueError):
    """Invalid snapshot, causal observation or exhausted replay envelope."""


class CellExplorerV3:
    """Bounded 4cm proprioceptive memory with explicit deterministic LRU."""

    def __init__(self):
        self.last_tick = -1
        self.updates = 0
        self.records = {}
        self.last_seen_at = {}
        self.evictions = 0
        self.forgotten_observations = 0

    @staticmethod
    def _cell(sense):
        if type(sense) is not Proprioception:
            raise CellV3Error("canonical proprioception required")
        # Preserve v2 quantization and innate action order up to 512 cells.
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
            "last_seen_at": dict(self.last_seen_at),
            "evictions": self.evictions,
            "forgotten_observations": self.forgotten_observations,
        }

    @classmethod
    def from_snapshot(cls, state):
        mandatory = {
            "schema", "cell_size_m", "last_tick", "updates",
            "records", "last_seen_at", "evictions", "forgotten_observations",
        }
        if (type(state) is not dict or set(state) != mandatory
                or state["schema"] != SCHEMA
                or type(state["cell_size_m"]) is not float
                or state["cell_size_m"] != STEP_METERS
                or type(state["last_tick"]) is not int
                or not -1 <= state["last_tick"] < MAX_EPISODE_TICKS
                or type(state["updates"]) is not int
                or state["updates"] != state["last_tick"] + 1
                or type(state["records"]) is not dict
                or type(state["last_seen_at"]) is not dict
                or not 0 <= len(state["records"]) <= MAX_CELLS
                or set(state["records"]) != set(state["last_seen_at"])
                or type(state["evictions"]) is not int
                or type(state["forgotten_observations"]) is not int
                or not 0 <= state["evictions"] <= state["forgotten_observations"]
                       <= state["updates"]):
            raise CellV3Error("invalid v3 retention envelope")
        observed_live = 0
        for key, actions in state["records"].items():
            if (type(key) is not str or _ZONE.fullmatch(key) is None
                    or any(int(c) > 25000 for c in key.split(":"))
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise CellV3Error("invalid v3 cell or action records")
            last_seen = state["last_seen_at"][key]
            if (type(last_seen) is not int
                    or not 0 <= last_seen <= state["last_tick"]):
                raise CellV3Error("invalid last observed cell tick")
            for counts in actions.values():
                if (type(counts) is not list or len(counts) != 2
                        or any(type(v) is not int for v in counts)
                        or not 1 <= counts[0] <= MAX_EPISODE_TICKS
                        or not 0 <= counts[1] <= counts[0]):
                    raise CellV3Error("invalid v3 measured counters")
                observed_live += counts[0]
        if observed_live + state["forgotten_observations"] != state["updates"]:
            raise CellV3Error("v3 lost or double-counted learned observations")
        if state["evictions"] == 0 and state["forgotten_observations"] != 0:
            raise CellV3Error("forgotten observations without eviction")
        if state["updates"] == 0 and state["records"]:
            raise CellV3Error("unobserved stored cells")

        model = cls()
        model.last_tick = state["last_tick"]
        model.updates = state["updates"]
        model.records = copy.deepcopy(state["records"])
        model.last_seen_at = dict(state["last_seen_at"])
        model.evictions = state["evictions"]
        model.forgotten_observations = state["forgotten_observations"]
        return model

    def decide(self, sense):
        if type(sense) is not Proprioception or sense.tick != self.last_tick + 1:
            raise CellV3Error("stale or invalid v3 proprioception tick")
        cell = self._cell(sense)
        stored = self.records.get(cell, {})
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        candidates = ACTIONS[shift:] + ACTIONS[:shift]
        selected = next(
            (name for name in candidates if stored.get(name, [0, 0])[1] == 0),
            "rest",
        )
        dx, dy = _MOVES[selected]
        return {
            "action": selected,
            "dx": dx, "dy": dy,
            "expected_revision": sense.revision, "tick": sense.tick,
            "zone": cell,
            "source": "bounded_goal_free_local_cell_motor_play_v3",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise CellV3Error("stale, forged or unmeasured v3 motor outcome")
        cell, action = intent["zone"], intent["action"]
        evicted = None
        dropped = 0
        if cell not in self.records and len(self.records) >= MAX_CELLS:
            # Stable tie break handles even synthetic snapshots with tied
            # last-seen stamps. No random seed or wall-clock dependence.
            evicted = min(
                self.last_seen_at,
                key=lambda k: (self.last_seen_at[k], k),
            )
            old = self.records.pop(evicted)
            dropped = sum(count[0] for count in old.values())
            del self.last_seen_at[evicted]
            self.evictions += 1
            self.forgotten_observations += dropped

        counts = self.records.setdefault(cell, {}).setdefault(
            action, [0, 0],
        )
        counts[0] += 1
        counts[1] += int(blocked)
        self.last_seen_at[cell] = sense.tick
        self.last_tick = sense.tick
        self.updates += 1
        return {
            "schema": LEARNING_SCHEMA,
            "action": action,
            "measured_blocked": blocked,
            "observed_outcomes": counts[0],
            "blocked_outcomes": counts[1],
            "total_updates": self.updates,
            "evicted_cell": evicted,
            "evicted_observations": dropped,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "source": "measured_synthetic_outcome_with_bounded_retention",
        }
