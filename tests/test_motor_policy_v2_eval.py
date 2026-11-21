"""Exact v2 policy evaluation versus legacy and frozen across 51 CPU scenes.

36 predeclared spatial/pulse fixtures plus 15 later *post-hoc* forced contacts.
Scores are QA diagnostics, NEVER policy rewards or claims of navigation.
"""
from __future__ import annotations

import json
import unittest

from mojum.developmental_policy import ACTIONS, Explorer, Proprioception, STEP_METERS
from mojum.developmental_policy_v2 import LocalCellExplorer, SCHEMA as V2_SCHEMA
from mojum.world_room import Room, advance, initial_state

TICKS = 120
PULSES = (
    ("alternating", tuple(i % 2 == 0 for i in range(TICKS))),
    ("all_true", (True,) * TICKS),
    ("phase_seven", tuple(i % 7 in (0, 1, 4) for i in range(TICKS))),
)
HELDOUT = (
    ("tiny-ne", Room(.16, .16), (.16, .16)),
    ("tiny-nw", Room(.16, .16), (0., .16)),
    ("tiny-se", Room(.16, .16), (.16, 0.)),
    ("tiny-sw", Room(.16, .16), (0., 0.)),
    ("small-center", Room(.32, .32), (.16, .16)),
    ("wall-left", Room(.64, .48, ((.28, .08, .08, .32),)), (.16, .24)),
    ("wall-right", Room(.64, .48, ((.28, .08, .08, .32),)), (.48, .24)),
    ("narrow-south", Room(.80, .40, ((.34, .12, .12, .28),)), (.40, .05)),
    ("wide-edge", Room(1.20, .80), (.05, .40)),
    ("open-center", Room(2., 2.), (1., 1.)),
    ("maze-left", Room(1.20, .90,
                       ((.40, 0., .08, .65), (.80, .25, .08, .65))),
     (.10, .10)),
    ("maze-right", Room(1.20, .90,
                        ((.40, 0., .08, .65), (.80, .25, .08, .65))),
     (1.05, .78)),
)
# Explicitly post-hoc: selected after seeing 23/36 original cases never
# touched a wall. These are NOT held-out generalization evidence.
CONTACT_CHALLENGES = (
    ("wall-face", Room(.64, .48, ((.28, .08, .08, .32),)), (.245, .24)),
    ("passage-face", Room(.80, .40, ((.34, .12, .12, .28),)), (.305, .20)),
    ("maze-face-one", Room(1.20, .90,
                           ((.40, 0., .08, .65), (.80, .25, .08, .65))),
     (.365, .30)),
    ("maze-face-two", Room(1.20, .90,
                           ((.40, 0., .08, .65), (.80, .25, .08, .65))),
     (.765, .35)),
    ("room-edge", Room(1.20, .80), (1.20, .40)),
)
_DELTAS = {
    "rest": (0., 0.), "x+": (STEP_METERS, 0.),
    "x-": (-STEP_METERS, 0.), "y+": (0., STEP_METERS),
    "y-": (0., -STEP_METERS),
}


def run(room, start, pulses, mode):
    assert mode in ("coarse_v1", "local_cell_v2", "frozen")
    state = initial_state(room, *start)
    learner = (Explorer() if mode == "coarse_v1" else
               LocalCellExplorer() if mode == "local_cell_v2" else None)
    rest = blocked = successful = longest = streak = 0
    previous_block = False
    moves = []
    for tick, pulse in enumerate(pulses):
        sense = Proprioception(tick, state.revision, state.x, state.y, pulse)
        if learner is None:
            action = ACTIONS[(tick + int(pulse)) % len(ACTIONS)]
            dx, dy = _DELTAS[action]
            intent = None
        else:
            intent = learner.decide(sense)
            action = intent["action"]
            dx, dy = intent["dx"], intent["dy"]
            assert intent["expected_revision"] == state.revision
        innate = ACTIONS[(tick + int(pulse)) % len(ACTIONS)]
        if action != innate:
            # Any deviation from the innate choice must follow a measured
            # collision on an earlier tick, never be a hidden goal oracle.
            assert previous_block
        state_next, outcome = advance(
            room, state, tick=tick + 1, dx=dx, dy=dy,
            expected_revision=state.revision,
        )
        if learner is not None:
            learner.observe(sense, intent, outcome["blocked"])
        previous_block |= outcome["blocked"]
        rest += action == "rest"
        blocked += outcome["blocked"]
        successful += action != "rest" and not outcome["blocked"]
        streak = streak + 1 if action == "rest" else 0
        longest = max(longest, streak)
        moves.append((action, outcome["blocked"]))
        state = state_next
    if learner is not None:
        assert learner.updates == len(pulses)
    return {
        "rest": rest, "blocked": blocked, "successful": successful,
        "attempted": len(pulses) - rest, "longest_rest": longest,
        "stalled": longest >= max(8, len(pulses) // 4),
        "moves": moves,
        "schema": learner.snapshot()["schema"] if learner is not None else None,
    }


def evaluate():
    summary = {
        "schema": "mojum.research.local-cell-v2-eval/1",
        "v2_policy_schema": V2_SCHEMA,
        "classification": "SYNTHETIC_MEASURED_QA_NOT_RELEASE",
        "cases": {"predeclared": len(HELDOUT) * len(PULSES),
                  "posthoc_contact": len(CONTACT_CHALLENGES) * len(PULSES)},
        "predeclared_stalls": {},
        "posthoc_stalls": {},
        "vs_frozen_predeclared": {"more": 0, "equal": 0, "less": 0},
        "predeclared_without_any_block": 0,
    }
    for group, scenes in (
        ("predeclared", HELDOUT),
        ("posthoc", CONTACT_CHALLENGES),
    ):
        stall = {name: 0 for name in ("coarse_v1", "local_cell_v2", "frozen")}
        for scene, room, start in scenes:
            for pulse_name, pulses in PULSES:
                results = {
                    name: run(room, start, pulses, name)
                    for name in stall
                }
                if group == "posthoc":
                    for data in results.values():
                        assert data["moves"][0] == ("x+", True), (
                            scene, pulse_name, "forced-contact fixture failed"
                        )
                if group == "predeclared" and not any(
                    data["blocked"] for data in results.values()
                ):
                    summary["predeclared_without_any_block"] += 1
                for name, data in results.items():
                    assert data["successful"] + data["blocked"] == data["attempted"]
                    stall[name] += int(data["stalled"])
                if group == "predeclared":
                    v2 = results["local_cell_v2"]["successful"]
                    frozen = results["frozen"]["successful"]
                    bucket = "more" if v2 > frozen else "less" if v2 < frozen else "equal"
                    summary["vs_frozen_predeclared"][bucket] += 1
        summary[group + "_stalls"] = stall
    return summary


class LocalCellV2EvaluationTests(unittest.TestCase):
    def test_versioned_policy_fidelity_and_heldout_diagnostics(self):
        stats = evaluate()
        self.assertEqual(stats["v2_policy_schema"], V2_SCHEMA)
        self.assertEqual(stats["cases"], {"predeclared": 36, "posthoc_contact": 15})
        self.assertEqual(stats["predeclared_without_any_block"], 23)
        self.assertEqual(stats["predeclared_stalls"],
                         {"coarse_v1": 10, "local_cell_v2": 0, "frozen": 0})
        self.assertEqual(stats["posthoc_stalls"],
                         {"coarse_v1": 6, "local_cell_v2": 0, "frozen": 0})
        self.assertEqual(stats["vs_frozen_predeclared"],
                         {"more": 5, "equal": 31, "less": 0})
        self.assertEqual(sum(stats["vs_frozen_predeclared"].values()), 36)

    def test_report_is_aggregate_only_and_never_exposes_sensor_trace(self):
        result = evaluate()
        wire = json.dumps(result, sort_keys=True, allow_nan=False)
        self.assertEqual(json.loads(wire), result)
        for hidden in ('"moves"', '"trace"', '"obstacles"', '"softness"',
                       '"genome"', '"pain"', '"reward"', '"valence"'):
            self.assertNotIn(hidden, wire)
        self.assertEqual(result["classification"],
                         "SYNTHETIC_MEASURED_QA_NOT_RELEASE")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--report"]:
        print(json.dumps(evaluate(), sort_keys=True, allow_nan=False))
    else:
        unittest.main()
