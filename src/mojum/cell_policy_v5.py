"""R0.15 opt-in v5 local-cell policy: bounded retention + bounded rest.

Continues the epoch-aware `local-cell/4` line without reinterpreting its
persisted evidence. It keeps v4's absolute-tick/epoch contract and
deterministic LRU budget and adds an explicit anti-stall rule so measured
blocked evidence can never collapse the policy into permanent rest.

Eviction remains a data-retention budget, NOT a negative experiential signal.
Rest is a bounded *probe pause*, not a goal, reward, pain or valence state.
No obstacle geometry, future sensory state, hardware or neural component is
read; only the agent's own epoch proprioception, its heartbeat pulse bit and
measured blocked outcomes.
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

SCHEMA = "mojum.developmental.local-cell/5"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/5"
MAX_CELLS = 512
# Consecutive rest decisions allowed before a deterministic re-probe.
MAX_REST_STREAK = 3
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
_MOVE_ACTIONS = tuple(name for name in ACTIONS if name != "rest")


class CellV5Error(ValueError):
    """Invalid v5 snapshot, causal observation or malformed transcript."""


class CellExplorerV5:
    """Bounded 4cm proprioceptive memory that cannot rest forever."""

    def __init__(self):
        self.last_tick = -1
        self.last_epoch_index = 0
        self.updates = 0
        self.records = {}
        self.last_seen_at = {}
        self.evictions = 0
        self.forgotten_observations = 0
        self.rest_streak = 0
        self.forced_probes = 0

    @staticmethod
    def _cell(sense):
        if type(sense) is not EpochProprioception:
            raise CellV5Error("canonical epoch proprioception required")
        # Identical quantization to v2/v3/v4 so retained evidence stays
        # comparable across versions without reinterpreting old journals.
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
            "rest_streak": self.rest_streak,
            "forced_probes": self.forced_probes,
        }

    @classmethod
    def from_snapshot(cls, state):
        mandatory = {
            "schema", "cell_size_m", "last_tick", "last_epoch_index",
            "updates", "records", "last_seen_at", "evictions",
            "forgotten_observations", "rest_streak", "forced_probes",
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
                       <= state["updates"]
                or type(state["rest_streak"]) is not int
                or not 0 <= state["rest_streak"] <= MAX_REST_STREAK
                or type(state["forced_probes"]) is not int
                or not 0 <= state["forced_probes"] <= state["updates"]):
            raise CellV5Error("invalid v5 retention envelope")
        observed_live = 0
        for key, actions in state["records"].items():
            if (type(key) is not str or _ZONE.fullmatch(key) is None
                    or any(int(c) > 25000 for c in key.split(":"))
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise CellV5Error("invalid v5 cell or action records")
            last_seen = state["last_seen_at"][key]
            if (type(last_seen) is not int
                    or not 0 <= last_seen <= state["last_tick"]):
                raise CellV5Error("invalid last observed cell tick")
            for action, counts in actions.items():
                if (type(counts) is not list or len(counts) != 2
                        or any(type(v) is not int for v in counts)
                        or not 1 <= counts[0] <= MAX_ABSOLUTE_TICKS
                        or not 0 <= counts[1] <= counts[0]):
                    raise CellV5Error("invalid v5 measured counters")
                if action == "rest" and counts[1]:
                    raise CellV5Error("impossible blocked rest history")
                observed_live += counts[0]
        if observed_live + state["forgotten_observations"] != state["updates"]:
            raise CellV5Error("v5 lost or double-counted learned observations")
        if state["evictions"] == 0 and state["forgotten_observations"] != 0:
            raise CellV5Error("forgotten observations without eviction")
        if state["updates"] == 0 and state["records"]:
            raise CellV5Error("unobserved stored cells")
        if state["updates"] and not state["records"]:
            raise CellV5Error("learned observations without a retained cell")
        # Exactly one cell is observed per absolute tick.
        stamps = list(state["last_seen_at"].values())
        if len(set(stamps)) != len(stamps):
            raise CellV5Error("multiple v5 cells observed on one tick")
        if state["updates"] and max(stamps) != state["last_tick"]:
            raise CellV5Error("latest v5 cell observation does not match tick")
        # The first eviction needs 512 distinct, previously observed cells.
        if state["evictions"] > max(0, state["updates"] - MAX_CELLS):
            raise CellV5Error("impossible v5 eviction history")
        # Eviction is replacement, not deletion: once full, the LRU table
        # remains at capacity for every later observation.
        if state["evictions"] and len(state["records"]) != MAX_CELLS:
            raise CellV5Error("evicted v5 table cannot be smaller than capacity")
        if state["rest_streak"]:
            if not state["last_seen_at"]:
                raise CellV5Error("rest streak without observations")
            # Rest has zero displacement: all consecutive rest decisions
            # must belong to the most recently observed cell.
            latest_cell = max(state["last_seen_at"], key=state["last_seen_at"].get)
            if state["records"][latest_cell].get("rest", [0, 0])[0] < state["rest_streak"]:
                raise CellV5Error("rest streak exceeds retained rest evidence")

        model = cls()
        model.last_tick = state["last_tick"]
        model.last_epoch_index = state["last_epoch_index"]
        model.updates = state["updates"]
        model.records = copy.deepcopy(state["records"])
        model.last_seen_at = dict(state["last_seen_at"])
        model.evictions = state["evictions"]
        model.forgotten_observations = state["forgotten_observations"]
        model.rest_streak = state["rest_streak"]
        model.forced_probes = state["forced_probes"]
        return model

    def decide(self, sense):
        if (type(sense) is not EpochProprioception
                or sense.tick != self.last_tick + 1):
            raise CellV5Error("stale or invalid v5 proprioception tick")
        cell = self._cell(sense)
        stored = self.records.get(cell, {})
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        rotation = ACTIONS[shift:] + ACTIONS[:shift]
        moves = [name for name in rotation if name != "rest"]
        untried = [name for name in moves if stored.get(name, [0, 0])[1] == 0]
        forced = False
        if untried:
            # A move with no measured block always outranks rest.
            selected = untried[0]
        elif self.rest_streak < MAX_REST_STREAK:
            selected = "rest"
        else:
            # Deterministic re-probe of the least-attempted direction.
            selected = min(
                moves,
                key=lambda name: (stored.get(name, [0, 0])[0], moves.index(name)),
            )
            forced = True
        dx, dy = _MOVES[selected]
        return {
            "action": selected,
            "dx": dx, "dy": dy,
            "expected_revision": sense.revision, "tick": sense.tick,
            "epoch_index": sense.epoch_index,
            "epoch_tick": sense.epoch_tick,
            "zone": cell,
            "forced_probe": forced,
            "source": "bounded_goal_free_local_cell_motor_play_v5",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise CellV5Error("stale, forged or unmeasured v5 motor outcome")
        cell, action = intent["zone"], intent["action"]
        if action == "rest" and blocked:
            raise CellV5Error("rest cannot be a blocked motor outcome")
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
        if action == "rest":
            self.rest_streak += 1
        else:
            self.rest_streak = 0
        if intent["forced_probe"]:
            self.forced_probes += 1
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
            "forced_probe": intent["forced_probe"],
            "rest_streak": self.rest_streak,
            "evicted_cell": evicted,
            "evicted_observations": dropped,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "source": "measured_synthetic_outcome_with_bounded_retention",
        }


def summarize_transcript(transcript):
    """Report movement effort and idle behaviour without reward semantics.

    `transcript` is a chronological iterable of ``(intent, outcome)`` pairs
    produced by this policy. The summary deliberately reports attempts,
    measured blocks, rest streaks and visited-cell coverage together, so a
    silent rest collapse cannot look like an improvement in blocked counts.
    """
    if isinstance(transcript, (str, bytes, dict)) or transcript is None:
        raise CellV5Error("transcript must be an ordered iterable of pairs")
    report = {
        "ticks": 0, "attempts": 0, "rest_steps": 0, "blocked": 0,
        "successful_moves": 0, "longest_rest_streak": 0,
        "idle_fraction": 0.0, "distinct_cells": 0, "forced_probes": 0,
    }
    streak = 0
    cells = set()
    for row in transcript:
        if type(row) is not tuple and type(row) is not list or len(row) != 2:
            raise CellV5Error("transcript rows must be (intent, outcome) pairs")
        intent, outcome = row
        if type(intent) is not dict or type(outcome) is not dict:
            raise CellV5Error("transcript rows must contain two mappings")
        action = intent.get("action")
        if type(action) is not str or action not in ACTIONS:
            raise CellV5Error("unknown transcript action")
        measured = outcome.get("measured_blocked")
        if type(measured) is not bool:
            raise CellV5Error("transcript outcome must carry a measured bool")
        forced = intent.get("forced_probe", False)
        if type(forced) is not bool:
            raise CellV5Error("malformed forced-probe marker")
        zone = intent.get("zone")
        if zone is not None:
            if type(zone) is not str or _ZONE.fullmatch(zone) is None:
                raise CellV5Error("malformed transcript cell key")
            cells.add(zone)
        if action == "rest" and measured:
            raise CellV5Error("rest cannot be recorded as blocked")
        report["ticks"] += 1
        report["blocked"] += int(measured)
        report["forced_probes"] += int(forced)
        if action == "rest":
            report["rest_steps"] += 1
            streak += 1
            report["longest_rest_streak"] = max(
                report["longest_rest_streak"], streak,
            )
        else:
            report["attempts"] += 1
            report["successful_moves"] += int(not measured)
            streak = 0
    if report["ticks"]:
        report["idle_fraction"] = report["rest_steps"] / report["ticks"]
    report["distinct_cells"] = len(cells)
    return report
