"""Opt-in local-cell policy that cannot learn itself into permanent rest.

Issue #106: a policy that permanently skips every action which has *ever*
produced one blocked outcome can rest forever once its four moves were blocked
once in the same coarse cell. ``Local Cell/2`` and ``Local Cell/3`` both fall
back to ``"rest"`` in that state, which silently inflates "safety" in
collision-only metrics while destroying mobility.

This is a new, explicitly versioned profile (``Local Cell/3-escape``). It keeps
per-4cm-cell measured evidence and v3 bounded retention, but adds one bounded
rule: when every move in the cell has measured blocked evidence, the policy
re-probes its own previously blocked actions instead of resting terminally.
After ``REPROBE_BLOCKED_BEFORE_REST`` consecutive failed probes it may rest for
at most ``MAX_CONSECUTIVE_RESTS`` ticks, then probes again. Rest stays a right,
never a terminal outcome.

Only the agent's own proprioception/pulse and its own measured blocked
outcomes are inputs: no room geometry, goal, reward, pain/valence, obstacle
oracle, neural brain or hardware actuation. Existing Local Cell/2 and Local
Cell/3 journals are never reinterpreted; this module does not touch them.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re

from .local_cell_sense import (
    ACTIONS, MAX_EPISODE_TICKS, Proprioception, STEP_METERS,
)
from .world_room import Room, advance, initial_state

SCHEMA = "mojum.developmental.local-cell/3-escape"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/3-escape"
BENCHMARK_SCHEMA = "mojum.developmental.local-cell-escape.benchmark/1"
MAX_CELLS = 512
# A rest episode may never exceed this many consecutive ticks.
MAX_CONSECUTIVE_RESTS = 3
# Bounded re-probing: rest only after this many consecutive failed probes in
# a cell where every move already has measured blocked evidence.
REPROBE_BLOCKED_BEFORE_REST = 4

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


class CellV3EscapeError(ValueError):
    """Invalid snapshot, causal observation or exhausted replay envelope."""


def _check_streak_counters(state):
    """Reject streak counters that no real observe() history can produce.

    observe() resets the blocked-probe streak on rest and the rest streak on
    any move, so both can never be non-zero together. The most recently
    observed cell is never evicted (eviction only happens when a new cell is
    being added, and that cell becomes the newest), so a live streak must be
    backed by an outcome of the right kind in the cell seen at last_tick.
    """
    rests = state["consecutive_rests"]
    probes = state["consecutive_blocked_probes"]
    updates = state["updates"]
    if rests and probes:
        raise CellV3EscapeError("rest and blocked-probe streaks coexist")
    if rests > updates or probes > updates:
        raise CellV3EscapeError("escape streak longer than observed history")
    if not (rests or probes):
        return
    last_cells = [
        key for key, seen in state["last_seen_at"].items()
        if seen == state["last_tick"]
    ]
    if len(last_cells) != 1:
        raise CellV3EscapeError("escape streak without a last observed cell")
    actions = state["records"][last_cells[0]]
    if rests and "rest" not in actions:
        raise CellV3EscapeError("rest streak without rest in last cell")
    if probes and not any(
        name != "rest" and counts[1] > 0 for name, counts in actions.items()
    ):
        raise CellV3EscapeError("blocked-probe streak without blocked move")
    # A terminal streak spans at most four consecutive ticks. All cells
    # observed during those ticks must still be in the 512-cell LRU table;
    # older evicted observations cannot substantiate a recent streak.
    if rests:
        measured_rests = sum(
            acts.get("rest", (0, 0))[0] for acts in state["records"].values()
        )
        if rests > measured_rests:
            raise CellV3EscapeError("rest streak lacks measured outcomes")
    if probes:
        measured_blocks = sum(
            counts[1] for acts in state["records"].values()
            for action, counts in acts.items() if action != "rest"
        )
        if probes > measured_blocks:
            raise CellV3EscapeError("blocked-probe streak lacks measured outcomes")
    # A retained cell last seen during the terminal streak must have at
    # least one measured outcome of the claimed kind. Global counts alone
    # cannot prove a streak: older rests/blocks may belong to unrelated ticks.
    first_streak_tick = state["last_tick"] - (rests or probes) + 1
    for cell, seen_at in state["last_seen_at"].items():
        if seen_at < first_streak_tick:
            continue
        recorded = state["records"][cell]
        if rests and "rest" not in recorded:
            raise CellV3EscapeError("recent cell lacks measured rest")
        if probes and not any(
            action != "rest" and counts[1] > 0
            for action, counts in recorded.items()
        ):
            raise CellV3EscapeError("recent cell lacks measured blocked move")


class CellExplorerV3Escape:
    """Bounded 4cm memory plus measured rest-escape probing (opt-in profile)."""

    def __init__(self):
        self.last_tick = -1
        self.updates = 0
        self.records = {}
        self.last_seen_at = {}
        self.evictions = 0
        self.forgotten_observations = 0
        self.consecutive_rests = 0
        self.consecutive_blocked_probes = 0
        self.outcome_tail = []

    @staticmethod
    def _cell(sense):
        if type(sense) is not Proprioception:
            raise CellV3EscapeError("canonical proprioception required")
        # Same deterministic quantization of the agent's own position as v3.
        return "%d:%d" % (
            round(sense.x / STEP_METERS),
            round(sense.y / STEP_METERS),
        )

    def snapshot(self):
        return {
            "schema": SCHEMA,
            "snapshot_version": 2,
            "cell_size_m": STEP_METERS,
            "last_tick": self.last_tick,
            "updates": self.updates,
            "records": copy.deepcopy(self.records),
            "last_seen_at": dict(self.last_seen_at),
            "evictions": self.evictions,
            "forgotten_observations": self.forgotten_observations,
            "consecutive_rests": self.consecutive_rests,
            "consecutive_blocked_probes": self.consecutive_blocked_probes,
            "outcome_tail": copy.deepcopy(self.outcome_tail),
        }

    @classmethod
    def from_snapshot(cls, state):
        legacy_mandatory = {
            "schema", "cell_size_m", "last_tick", "updates", "records",
            "last_seen_at", "evictions", "forgotten_observations",
            "consecutive_rests", "consecutive_blocked_probes",
        }
        if type(state) is dict and set(state) == legacy_mandatory:
            if state.get("consecutive_rests") or state.get("consecutive_blocked_probes"):
                raise CellV3EscapeError(
                    "legacy snapshot lacks chronology evidence for strict streak attestation"
                )
            state = dict(state)
            state["snapshot_version"] = 2
            state["outcome_tail"] = []
        mandatory = legacy_mandatory | {"snapshot_version", "outcome_tail"}
        if (type(state) is not dict or set(state) != mandatory
                or state["schema"] != SCHEMA
                or type(state["snapshot_version"]) is not int
                or state["snapshot_version"] != 2
                or type(state["outcome_tail"]) is not list
                or len(state["outcome_tail"]) > REPROBE_BLOCKED_BEFORE_REST
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
                       <= state["updates"]
                or type(state["consecutive_rests"]) is not int
                or not 0 <= state["consecutive_rests"] <= MAX_CONSECUTIVE_RESTS
                or type(state["consecutive_blocked_probes"]) is not int
                or not 0 <= state["consecutive_blocked_probes"]
                       <= REPROBE_BLOCKED_BEFORE_REST):
            raise CellV3EscapeError("invalid escape retention envelope")
        if state["updates"] == 0 and (state["consecutive_rests"]
                                      or state["consecutive_blocked_probes"]):
            raise CellV3EscapeError("unobserved escape counters")
        observed_live = 0
        observations_by_tick = {}
        for key, actions in state["records"].items():
            if (type(key) is not str or _ZONE.fullmatch(key) is None
                    or any(int(c) > 25000 for c in key.split(":"))
                    or type(actions) is not dict or not actions
                    or not set(actions) <= set(ACTIONS)):
                raise CellV3EscapeError("invalid escape cell or action records")
            last_seen = state["last_seen_at"][key]
            if (type(last_seen) is not int
                    or not 0 <= last_seen <= state["last_tick"]):
                raise CellV3EscapeError("invalid last observed escape cell tick")
            cell_observations = 0
            for action, counts in actions.items():
                if (type(counts) is not list or len(counts) != 2
                        or any(type(v) is not int for v in counts)
                        or not 1 <= counts[0] <= MAX_EPISODE_TICKS
                        or not 0 <= counts[1] <= counts[0]):
                    raise CellV3EscapeError("invalid escape measured counters")
                # Unlike v3, a re-probe may observe the same action blocked
                # again, so blocked counts above one are legitimate here.
                if action == "rest" and counts[1]:
                    raise CellV3EscapeError("impossible blocked rest history")
                cell_observations += counts[0]
            # Each tick measures at most one cell. A cell last observed at
            # tick t therefore cannot contain more than t + 1 outcomes.
            if cell_observations > last_seen + 1:
                raise CellV3EscapeError(
                    "more escape cell observations than elapsed ticks"
                )
            if last_seen in observations_by_tick:
                raise CellV3EscapeError("multiple escape cells observed on one tick")
            observations_by_tick[last_seen] = cell_observations
            observed_live += cell_observations
        if observed_live + state["forgotten_observations"] != state["updates"]:
            raise CellV3EscapeError("escape lost or double-counted observations")
        if state["evictions"] == 0 and state["forgotten_observations"] != 0:
            raise CellV3EscapeError("forgotten observations without eviction")
        if state["updates"] == 0 and state["records"]:
            raise CellV3EscapeError("unobserved stored escape cells")
        _check_streak_counters(state)
        tail = state["outcome_tail"]
        expected_tail_length = min(state["updates"], REPROBE_BLOCKED_BEFORE_REST)
        if len(tail) != expected_tail_length:
            raise CellV3EscapeError("incomplete measured-outcome chronology tail")
        tail_counts = {}
        for offset, item in enumerate(tail):
            if (type(item) is not dict or set(item) != {"tick", "cell", "action", "blocked"}
                    or type(item["tick"]) is not int
                    or item["tick"] != state["last_tick"] - len(tail) + 1 + offset
                    or type(item["cell"]) is not str
                    or _ZONE.fullmatch(item["cell"]) is None
                    or item["cell"] not in state["records"]
                    or item["action"] not in ACTIONS
                    or type(item["blocked"]) is not bool
                    or (item["action"] == "rest" and item["blocked"])
                    or state["last_seen_at"][item["cell"]] < item["tick"]):
                raise CellV3EscapeError("invalid measured-outcome chronology tail")
            key = (item["cell"], item["action"])
            counts = tail_counts.setdefault(key, [0, 0])
            counts[0] += 1
            counts[1] += int(item["blocked"])
        for (cell, action), counts in tail_counts.items():
            stored = state["records"][cell][action]
            if stored[0] < counts[0] or stored[1] < counts[1]:
                raise CellV3EscapeError("chronology tail exceeds measured aggregates")
        rest_suffix = 0
        for item in reversed(tail):
            if item["action"] == "rest" and not item["blocked"]:
                rest_suffix += 1
            else:
                break
        blocked_suffix = 0
        for item in reversed(tail):
            if item["action"] != "rest" and item["blocked"]:
                blocked_suffix += 1
            else:
                break
        if (rest_suffix != state["consecutive_rests"]
                or blocked_suffix != state["consecutive_blocked_probes"]):
            raise CellV3EscapeError("terminal streak counters disagree with ordered outcomes")

        # Each observation belongs to exactly one cell and tick. A live
        # snapshot must retain the latest observed cell, and an eviction can
        # only happen after the bounded table has first reached capacity.
        if state["updates"] and not state["records"]:
            raise CellV3EscapeError("escape updates without a retained cell")
        if state["updates"] and max(observations_by_tick) != state["last_tick"]:
            raise CellV3EscapeError("latest escape cell observation does not match tick")
        if state["evictions"] > max(0, state["updates"] - MAX_CELLS):
            raise CellV3EscapeError("impossible escape eviction history")
        if state["evictions"] and len(state["records"]) != MAX_CELLS:
            raise CellV3EscapeError("escape retention shrank after eviction")

        # Evicted LRU observations predate the last observation of every
        # retained cell. Their cumulative count must fit each tick prefix;
        # per-cell deadlines alone do not guarantee a possible history.
        observed_prefix = state["forgotten_observations"]
        for tick, count in sorted(observations_by_tick.items()):
            observed_prefix += count
            if observed_prefix > tick + 1:
                raise CellV3EscapeError(
                    "combined escape cell observations exceed elapsed ticks"
                )

        model = cls()
        model.last_tick = state["last_tick"]
        model.updates = state["updates"]
        model.records = copy.deepcopy(state["records"])
        model.last_seen_at = dict(state["last_seen_at"])
        model.evictions = state["evictions"]
        model.forgotten_observations = state["forgotten_observations"]
        model.consecutive_rests = state["consecutive_rests"]
        model.consecutive_blocked_probes = state["consecutive_blocked_probes"]
        model.outcome_tail = copy.deepcopy(state["outcome_tail"])
        return model

    def decide(self, sense):
        if type(sense) is not Proprioception or sense.tick != self.last_tick + 1:
            raise CellV3EscapeError("stale or invalid escape proprioception tick")
        cell = self._cell(sense)
        stored = self.records.get(cell, {})
        shift = (sense.tick + int(sense.pulse)) % len(ACTIONS)
        rotation = ACTIONS[shift:] + ACTIONS[:shift]
        unblocked_moves = [
            name for name in rotation
            if name != "rest" and stored.get(name, (0, 0))[1] == 0
        ]
        if unblocked_moves:
            # Preserve the innate rotation, including bounded rest, but never
            # let the rest budget hide moves that were measured as available.
            selected = ("rest" if rotation.index("rest") < rotation.index(unblocked_moves[0])
                        else unblocked_moves[0])
            if (selected == "rest"
                    and self.consecutive_rests >= MAX_CONSECUTIVE_RESTS):
                selected = unblocked_moves[0]
            elif (selected != "rest"
                    and self.consecutive_rests < MAX_CONSECUTIVE_RESTS
                    and self.consecutive_blocked_probes
                        >= REPROBE_BLOCKED_BEFORE_REST):
                # The pause after a measured bout of failed probes is a
                # property of the streak, not of the current cell. A fresh or
                # foreign cell cannot extend it beyond the versioned ceiling.
                selected = "rest"
        elif (self.consecutive_rests < MAX_CONSECUTIVE_RESTS
                and self.consecutive_blocked_probes >= REPROBE_BLOCKED_BEFORE_REST):
            # One bounded pause after a measured bout of failed probes.
            selected = "rest"
        else:
            # Every move in this cell was blocked at least once. Re-probe the
            # agent's own evidence instead of resting forever.
            selected = next(name for name in rotation if name != "rest")
        dx, dy = _MOVES[selected]
        return {
            "action": selected,
            "dx": dx, "dy": dy,
            "expected_revision": sense.revision, "tick": sense.tick,
            "zone": cell,
            "source": "bounded_goal_free_local_cell_escape_probe_v3e",
        }

    def observe(self, sense, intent, blocked):
        if type(blocked) is not bool or intent != self.decide(sense):
            raise CellV3EscapeError("stale, forged or unmeasured escape outcome")
        cell, action = intent["zone"], intent["action"]
        # Rest is a zero-displacement choice, not a collision measurement.
        # Never commit a state that fails our own snapshot validation.
        if action == "rest" and blocked:
            raise CellV3EscapeError("rest cannot be a blocked outcome")
        evicted = None
        dropped = 0
        if cell not in self.records and len(self.records) >= MAX_CELLS:
            evicted = min(
                self.last_seen_at,
                key=lambda k: (self.last_seen_at[k], k),
            )
            old = self.records.pop(evicted)
            dropped = sum(count[0] for count in old.values())
            del self.last_seen_at[evicted]
            self.evictions += 1
            self.forgotten_observations += dropped

        counters = self.records.setdefault(cell, {}).setdefault(
            action, [0, 0],
        )
        prior_blocked = counters[1]
        counters[0] += 1
        counters[1] += int(blocked)
        self.last_seen_at[cell] = sense.tick
        self.last_tick = sense.tick
        self.updates += 1
        if action == "rest":
            self.consecutive_rests += 1
            self.consecutive_blocked_probes = 0
        else:
            self.consecutive_rests = 0
            self.consecutive_blocked_probes = (
                self.consecutive_blocked_probes + 1 if blocked else 0
            )
        self.outcome_tail.append({
            "tick": sense.tick, "cell": cell, "action": action, "blocked": blocked,
        })
        del self.outcome_tail[:-REPROBE_BLOCKED_BEFORE_REST]
        return {
            "schema": LEARNING_SCHEMA,
            "action": action,
            "measured_blocked": blocked,
            "observed_outcomes": counters[0],
            "blocked_outcomes": counters[1],
            "escape_probe": action != "rest" and prior_blocked > 0,
            "consecutive_rests": self.consecutive_rests,
            "consecutive_blocked_probes": self.consecutive_blocked_probes,
            "total_updates": self.updates,
            "evicted_cell": evicted,
            "evicted_observations": dropped,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "source": "measured_synthetic_outcome_with_bounded_escape",
        }


# --- isolated comparison profile (no Aquarium manifest or SQLite changes) ---

REST_GATE_IDLE_FRACTION = 0.5
REST_GATE_MIN_STREAK = 10


def sealed_cell():
    """A point-room cell whose four 4 cm moves are all blocked by thin walls."""
    room = Room(0.30, 0.30, (
        (0.140, 0.10, 0.002, 0.10),
        (0.158, 0.10, 0.002, 0.10),
        (0.10, 0.140, 0.10, 0.002),
        (0.10, 0.158, 0.10, 0.002),
    ))
    return room, (0.150, 0.150)


def open_room():
    return Room(4, 4), (1.0, 2.0)


def corner_room():
    return Room(0.16, 0.16), (0.16, 0.16)


def _room_report(room):
    return {
        "width_m": room.width,
        "height_m": room.height,
        "obstacles": [list(rect) for rect in room.obstacles],
        "source": "deterministic-point-room-fixture/v1",
    }


def run_policy(policy_factory, room, start, *, ticks):
    """Drive one policy through identical point-room ticks and report measures."""
    if type(ticks) is not int or not 1 <= ticks <= MAX_EPISODE_TICKS:
        raise CellV3EscapeError("invalid tick budget")
    policy = policy_factory() if callable(policy_factory) else policy_factory
    if not hasattr(policy, "decide") or not hasattr(policy, "observe"):
        raise CellV3EscapeError("policy must expose decide/observe")
    state = initial_state(room, start[0], start[1])
    previous = (state.x, state.y)
    positions = {previous}
    rest_actions = move_actions = blocked_attempts = successful_moves = 0
    longest_rest_streak = streak = 0
    path_length = 0.0
    actions = []
    for tick in range(ticks):
        sense = Proprioception(
            tick, state.revision, state.x, state.y, tick % 2 == 0,
        )
        intent = policy.decide(sense)
        state, result = advance(
            room, state, tick=tick + 1, dx=intent["dx"], dy=intent["dy"],
            expected_revision=state.revision,
        )
        policy.observe(sense, intent, result["blocked"])
        action = intent["action"]
        actions.append(action)
        position = (state.x, state.y)
        positions.add(position)
        path_length += math.hypot(
            position[0] - previous[0], position[1] - previous[1],
        )
        previous = position
        if action == "rest":
            rest_actions += 1
            streak += 1
            longest_rest_streak = max(longest_rest_streak, streak)
        else:
            move_actions += 1
            streak = 0
            if result["blocked"]:
                blocked_attempts += 1
            else:
                successful_moves += 1
    idle_fraction = rest_actions / ticks
    rest_gate = bool(
        idle_fraction >= REST_GATE_IDLE_FRACTION
        and longest_rest_streak >= max(REST_GATE_MIN_STREAK, ticks // 10)
    )
    if rest_gate:
        verdict = "REST_COLLAPSE_SUSPECTED"
    elif path_length == 0.0:
        verdict = "NO_MEASURED_DISPLACEMENT"
    else:
        verdict = "MOBILITY_OBSERVED"
    return {
        "policy": type(policy).__name__,
        "frames": ticks,
        "rest_actions": rest_actions,
        "move_actions": move_actions,
        "blocked_attempts": blocked_attempts,
        "successful_moves": successful_moves,
        "idle_fraction": idle_fraction,
        "longest_rest_streak": longest_rest_streak,
        "distinct_positions": len(positions),
        "path_length_m": path_length,
        "actions_sha256": _actions_digest(actions),
        "verdict": verdict,
        "source": "synthetic_point_room_not_navigation_score",
        "limits": {
            "rest_gate_idle_fraction": REST_GATE_IDLE_FRACTION,
            "rest_gate_min_streak": REST_GATE_MIN_STREAK,
            "max_consecutive_rests": MAX_CONSECUTIVE_RESTS,
            "reprobe_blocked_before_rest": REPROBE_BLOCKED_BEFORE_REST,
        },
    }


def _actions_digest(actions):
    import hashlib
    payload = json.dumps(actions, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def compare_policies(*, ticks=60):
    """Compare Local Cell/3 with the escape profile on identical scenarios."""
    scenarios = {
        "sealed": sealed_cell(),
        "open": open_room(),
        "corner": corner_room(),
    }
    results = {}
    for name, (room, start) in scenarios.items():
        results[name] = {
            "room": _room_report(room),
            "start": [start[0], start[1]],
            "pulse_stream": "tick_even_heartbeat_pulse",
            "policies": {
                "v3": run_policy(_v3, room, start, ticks=ticks),
                "v3_escape": run_policy(CellExplorerV3Escape, room, start, ticks=ticks),
            },
        }
    return {
        "schema": BENCHMARK_SCHEMA,
        "ticks": ticks,
        "policies": results["sealed"]["policies"],
        "scenarios": results,
        "interpretation": (
            "bounded_synthetic_escape_benchmark_not_organism_release_evidence"
        ),
    }


def _v3():
    from .cell_policy_v3 import CellExplorerV3
    return CellExplorerV3()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=60)
    args = parser.parse_args()
    try:
        report = compare_policies(ticks=args.ticks)
    except (CellV3EscapeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
