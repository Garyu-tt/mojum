"""Versioned local-cell motor policy with explicit absolute ticks and epochs.

The policy is bounded measured-outcome retention for the synthetic point room.
It is not a neural brain, reward optimizer, affect model, geometry oracle, or
physical motor authority.  Eviction is a storage budget, not a signal.
"""

from __future__ import annotations

import copy
import re

from .epoch_sense_v2 import (
    EPOCH_TICKS,
    EpochProprioception,
    MAX_ABSOLUTE_TICKS,
)
from .local_cell_sense import ACTIONS, STEP_METERS


SCHEMA = "mojum.developmental.local-cell/4"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/4"
MAX_CELLS = 512
_ZONE = re.compile(r"(?:0|[1-9][0-9]{0,4}):(?:0|[1-9][0-9]{0,4})\Z", re.ASCII)
_MOVES = {
    "rest": (0.0, 0.0),
    "x+": (STEP_METERS, 0.0),
    "y+": (0.0, STEP_METERS),
    "x-": (-STEP_METERS, 0.0),
    "y-": (0.0, -STEP_METERS),
}


class CellV4Error(ValueError):
    """Invalid snapshot, causal observation, or bounded replay state."""


class CellExplorerV4:
    """Bounded 4 cm proprioceptive memory with deterministic LRU retention."""

    def __init__(self):
        self.last_tick = -1
        self.last_epoch_index = 0
        self.updates = 0
        self.records = {}
        self.last_seen_at = {}
        self.evictions = 0
        self.forgotten_observations = 0

    @staticmethod
    def _cell(sense):
        if type(sense) is not EpochProprioception:
            raise CellV4Error("canonical epoch proprioception required")
        return "%d:%d" % (
            round(sense.x / STEP_METERS),
            round(sense.y / STEP_METERS),
        )

    def snapshot(self):
        return {
            "schema": SCHEMA,
            "cell_size_m": STEP_METERS,
            "last_tick": self.last_tick,
            "last_epoch_index": self.last_epoch_index,
            "updates": self.updates,
            "records": copy.deepcopy(self.records),
            "last_seen_at": dict(self.last_seen_at),
            "evictions": self.evictions,
            "forgotten_observations": self.forgotten_observations,
        }

    @classmethod
    def from_snapshot(cls, state):
        mandatory = {
            "schema", "cell_size_m", "last_tick", "last_epoch_index", "updates",
            "records", "last_seen_at", "evictions", "forgotten_observations",
        }
        if (type(state) is not dict or set(state) != mandatory
                or state["schema"] != SCHEMA
                or type(state["cell_size_m"]) is not float
                or state["cell_size_m"] != STEP_METERS
                or type(state["last_tick"]) is not int
                or not -1 <= state["last_tick"] < MAX_ABSOLUTE_TICKS
                or type(state["last_epoch_index"]) is not int
                or state["last_epoch_index"] != max(0, state["last_tick"] // EPOCH_TICKS)
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
            raise CellV4Error("invalid v4 retention envelope")

        observed_live = 0
        for key, actions in state["records"].items():
            if (type(key) is not str or _ZONE.fullmatch(key) is None
                    or any(int(c) > 25000 for c in key.split(":"))
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise CellV4Error("invalid v4 cell or action records")
            last_seen = state["last_seen_at"][key]
            if type(last_seen) is not int or not 0 <= last_seen <= state["last_tick"]:
                raise CellV4Error("invalid last observed cell tick")
            cell_observations = 0
            for action, counts in actions.items():
                if (type(counts) is not list or len(counts) != 2
                        or any(type(v) is not int for v in counts)
                        or not 1 <= counts[0] <= MAX_ABSOLUTE_TICKS
                        or not 0 <= counts[1] <= counts[0]
                        or counts[1] > 1
                        or (action == "rest" and counts[1])):
                    raise CellV4Error("invalid v4 measured counters")
                cell_observations += counts[0]
            # A cell can be observed at most once per absolute tick, and ticks
            # start at 0, so it cannot hold more observations than elapsed ticks.
            if cell_observations > last_seen + 1:
                raise CellV4Error("more v4 cell observations than elapsed ticks")
            observed_live += cell_observations
        if observed_live + state["forgotten_observations"] != state["updates"]:
            raise CellV4Error("v4 lost or double-counted learned observations")
        if state["evictions"] == 0 and state["forgotten_observations"] != 0:
            raise CellV4Error("forgotten observations without eviction")
        if state["updates"] == 0 and state["records"]:
            raise CellV4Error("unobserved stored cells")
        if state["updates"] and not state["records"]:
            raise CellV4Error("learned observations without a retained cell")
        # One observation per absolute tick, so two live cells cannot share a
        # last-seen tick and the newest stamp must be the current tick.
        stamps = list(state["last_seen_at"].values())
        if len(set(stamps)) != len(stamps):
            raise CellV4Error("multiple v4 cells observed on one tick")
        if state["updates"] and max(stamps) != state["last_tick"]:
            raise CellV4Error("latest v4 cell observation does not match tick")
        # Every eviction needs at least one observation of a distinct cell,
        # so a full table plus E evictions requires MAX_CELLS + E updates.
        if state["evictions"] > max(0, state["updates"] - MAX_CELLS):
            raise CellV4Error("impossible v4 eviction history")
        # Eviction only happens when a new cell arrives at a full table, and
        # the retained table never shrinks afterwards. A history that claims
        # an eviction while retaining fewer than MAX_CELLS is unreachable.
        if state["evictions"] and len(state["records"]) != MAX_CELLS:
            raise CellV4Error("impossible v4 eviction history")

        model = cls()
        model.last_tick = state["last_tick"]
        model.last_epoch_index = state["last_epoch_index"]
        model.updates = state["updates"]
        model.records = copy.deepcopy(state["records"])
        model.last_seen_at = dict(state["last_seen_at"])
        model.evictions = state["evictions"]
        model.forgotten_observations = state["forgotten_observations"]
        return model

    def decide(self, sense):
        if (type(sense) is not EpochProprioception
                or sense.tick != self.last_tick + 1):
            raise CellV4Error("stale or invalid v4 proprioception tick")
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
            "dx": dx,
            "dy": dy,
            "expected_revision": sense.revision,
            "tick": sense.tick,
            "epoch_index": sense.epoch_index,
            "epoch_tick": sense.epoch_tick,
            "zone": cell,
            "source": "bounded_goal_free_local_cell_motor_play_v4",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise CellV4Error("stale, forged or unmeasured v4 motor outcome")
        cell, action = intent["zone"], intent["action"]
        if action == "rest" and blocked:
            raise CellV4Error("blocked rest is impossible")
        evicted = None
        dropped = 0
        if cell not in self.records and len(self.records) >= MAX_CELLS:
            evicted = min(self.last_seen_at, key=lambda k: (self.last_seen_at[k], k))
            old = self.records.pop(evicted)
            dropped = sum(count[0] for count in old.values())
            del self.last_seen_at[evicted]
            self.evictions += 1
            self.forgotten_observations += dropped

        counts = self.records.setdefault(cell, {}).setdefault(action, [0, 0])
        counts[0] += 1
        counts[1] += int(blocked)
        self.last_seen_at[cell] = sense.tick
        self.last_tick = sense.tick
        self.last_epoch_index = sense.epoch_index
        self.updates += 1
        return {
            "schema": LEARNING_SCHEMA,
            "action": action,
            "measured_blocked": blocked,
            "observed_outcomes": counts[0],
            "blocked_outcomes": counts[1],
            "total_updates": self.updates,
            "epoch_index": sense.epoch_index,
            "epoch_tick": sense.epoch_tick,
            "evicted_cell": evicted,
            "evicted_observations": dropped,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "source": "measured_synthetic_outcome_with_bounded_retention",
        }
