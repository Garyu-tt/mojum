"""R0.14 held-out, no-oracle QA for the three synthetic neutral-probe arms.

These fixtures are declared in source before looking at their outcomes.
A research policy must not be promoted based on one tiny corner example.
The report has no PRIVATE Noosphere state and is not a reward function.
"""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch

from mojum.developmental_policy import Explorer, Proprioception
from mojum.neutral_probe_trial import (
    COMPARISON_SCHEMA, LocalCellPlay, compare,
)
from mojum.world_room import Room

TICKS = 120

# Distinct room sizes, each corner, central area, wall occlusion and
# two-obstacle corridors. Initial positions are all legal world positions.
SCENES = (
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
                       ((.40, .0, .08, .65), (.80, .25, .08, .65))),
     (.10, .10)),
    ("maze-right", Room(1.20, .90,
                        ((.40, .0, .08, .65), (.80, .25, .08, .65))),
     (1.05, .78)),
)

PULSES = (
    ("alternating", tuple(i % 2 == 0 for i in range(TICKS))),
    ("all-true", (True,) * TICKS),
    ("phase-seven", tuple(i % 7 in (0, 1, 4) for i in range(TICKS))),
)
ARM_NAMES = ("adaptive", "frozen", "local_cell")
# Trace contains only synthetic observations, actions, measured outcomes.
TRACE_FIELDS = frozenset({
    "tick", "heartbeat_pulse", "action", "requested_delta_xy",
    "blocked", "position_xy",
})


def scenarios():
    for scene_name, room, start in SCENES:
        for pulse_name, pulses in PULSES:
            yield scene_name, pulse_name, room, start, pulses


def score(arm):
    """Display-only factual diagnostics; never fed back as learner reward."""
    return {
        "attempted": arm["attempted_movement_count"],
        "successful": arm["successful_movement_count"],
        "blocked": arm["blocked_count"],
        "rest": arm["rest_count"],
        "longest_rest": arm["longest_rest_streak"],
        "unique_positions": arm["unique_positions_1um"],
        "stalled": arm["stalled_warning"],
    }


def report():
    """A reproducible aggregate report with no trajectory or model contents."""
    matrix = []
    for scene, pulse_name, room, start, pulses in scenarios():
        result = compare(room=room, start=start, pulses=pulses)
        matrix.append({
            "scene": scene, "pulse": pulse_name,
            "adaptive": score(result["adaptive"]),
            "frozen": score(result["frozen"]),
            "local_cell": score(result["local_cell"]),
        })
    n = len(matrix)
    return {
        "schema": "mojum.research.heldout-neutral-probe-report/1",
        "classification": "SYNTHETIC_OFFLINE_QA_NOT_POLICY_RELEASE",
        "ticks_per_case": TICKS,
        "cases": n,
        "stalled_by_arm": {
            name: sum(row[name]["stalled"] for row in matrix)
            for name in ARM_NAMES
        },
        "local_vs_frozen": {
            "more_successful_motion": sum(
                row["local_cell"]["successful"] > row["frozen"]["successful"]
                for row in matrix),
            "less_successful_motion": sum(
                row["local_cell"]["successful"] < row["frozen"]["successful"]
                for row in matrix),
            "equal_successful_motion": sum(
                row["local_cell"]["successful"] == row["frozen"]["successful"]
                for row in matrix),
        },
        "matrix": matrix,
        "conclusion": "DESCRIPTIVE_ONLY_NO_UNIVERSAL_POLICY_WIN",
    }


class NeutralProbeHeldoutTests(unittest.TestCase):
    def test_starts_pulses_metrics_and_causal_trace_contract(self):
        for scene, pulse_name, room, start, pulses in scenarios():
            with self.subTest(scene=scene, pulse=pulse_name):
                result = compare(room=room, start=start, pulses=pulses)
                self.assertEqual(result["schema"], COMPARISON_SCHEMA)
                arms = [result[name] for name in ARM_NAMES]
                self.assertEqual([a["mode"] for a in arms], list(ARM_NAMES))
                self.assertEqual(arms[0]["trace"][0]["action"],
                                 arms[1]["trace"][0]["action"])
                self.assertEqual(arms[0]["trace"][0]["action"],
                                 arms[2]["trace"][0]["action"])
                for arm in arms:
                    trace = arm["trace"]
                    self.assertEqual(len(trace), TICKS)
                    self.assertEqual([f["tick"] for f in trace],
                                     list(range(TICKS)))
                    self.assertEqual(tuple(f["heartbeat_pulse"] for f in trace),
                                     pulses)
                    self.assertTrue(all(set(frame) == TRACE_FIELDS
                                        for frame in trace))
                    self.assertEqual(sum(f["action"] == "rest" for f in trace),
                                     arm["rest_count"])
                    self.assertEqual(sum(f["blocked"] for f in trace),
                                     arm["blocked_count"])
                    self.assertEqual(sum(
                        f["action"] != "rest" and not f["blocked"]
                        for f in trace), arm["successful_movement_count"])
                    self.assertEqual(
                        arm["attempted_movement_count"] +
                        arm["rest_count"], TICKS)
                    self.assertEqual(
                        arm["successful_movement_count"] +
                        arm["blocked_count"],
                        arm["attempted_movement_count"])
                    longest = running = 0
                    for frame in trace:
                        running = running + 1 if frame["action"] == "rest" else 0
                        longest = max(longest, running)
                    self.assertEqual(arm["longest_rest_streak"], longest)
                    self.assertEqual(arm["stalled_warning"],
                                     longest >= max(8, TICKS // 4))
                    self.assertGreaterEqual(arm["unique_positions_1um"], 1)
                    self.assertLessEqual(arm["unique_positions_1um"], TICKS)
                    self.assertEqual(arm["classification"],
                                     "SYNTHETIC_POINT_ROOM_RESEARCH_ONLY")
                self.assertIsNone(result["frozen"]["learned_state"])
                self.assertEqual(result["adaptive"]["learned_state"]["updates"],
                                 TICKS)
                self.assertEqual(result["local_cell"]["learned_state"]["updates"],
                                 TICKS)

    def test_action_divergence_requires_prior_measured_block(self):
        # A learned action is allowed to differ from the frozen baseline
        # only after an actual measured collision could have changed it.
        # This tests causality without asserting that every learner wins.
        for scene, pulse_name, room, start, pulses in scenarios():
            with self.subTest(scene=scene, pulse=pulse_name):
                result = compare(room=room, start=start, pulses=pulses)
                frozen = result["frozen"]["trace"]
                for name in ("adaptive", "local_cell"):
                    other = result[name]["trace"]
                    diverged = False
                    measured_block_before = False
                    for current, baseline in zip(other, frozen):
                        if current["action"] != baseline["action"]:
                            diverged = True
                            self.assertTrue(measured_block_before)
                        measured_block_before |= current["blocked"]
                    if not measured_block_before:
                        self.assertFalse(diverged)

    def test_policy_receives_only_typed_proprioception_not_world_map(self):
        original_adaptive = Explorer.decide
        original_local = LocalCellPlay.decide
        observed = []

        def inspect_adaptive(policy, sense):
            self.assertIs(type(sense), Proprioception)
            self.assertEqual(set(vars(sense)),
                             {"tick", "revision", "x", "y", "pulse"})
            observed.append("adaptive")
            return original_adaptive(policy, sense)

        def inspect_local(policy, sense):
            self.assertIs(type(sense), Proprioception)
            self.assertEqual(set(vars(sense)),
                             {"tick", "revision", "x", "y", "pulse"})
            observed.append("local_cell")
            return original_local(policy, sense)

        with (patch.object(Explorer, "decide", inspect_adaptive),
              patch.object(LocalCellPlay, "decide", inspect_local)):
            scene, pulse_name, room, start, pulses = list(scenarios())[5]
            compare(room=room, start=start, pulses=pulses)
        self.assertIn("adaptive", observed)
        self.assertIn("local_cell", observed)

    def test_report_discloses_only_aggregate_and_never_proclaims_winner(self):
        result = report()
        self.assertEqual(result["cases"], len(SCENES) * len(PULSES))
        self.assertEqual(result["ticks_per_case"], TICKS)
        self.assertEqual(result["conclusion"],
                         "DESCRIPTIVE_ONLY_NO_UNIVERSAL_POLICY_WIN")
        self.assertEqual(sum(result["local_vs_frozen"].values()),
                         result["cases"])
        wire = json.dumps(result, sort_keys=True, allow_nan=False)
        self.assertEqual(json.loads(wire), result)
        for forbidden in ('"trace"', '"learned_state"', '"obstacles"',
                          '"reward"', '"pain"', '"valence"', '"agent_id"'):
            self.assertNotIn(forbidden, wire)
        # Keep a verifiable known defect visible; never silently report the
        # accepted R0.13 coarse-zone policy as broadly solved by this study.
        ne = next(row for row in result["matrix"]
                  if row["scene"] == "tiny-ne" and row["pulse"] == "alternating")
        self.assertTrue(ne["adaptive"]["stalled"])
        self.assertFalse(ne["frozen"]["stalled"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        print(json.dumps(report(), sort_keys=True,
                         ensure_ascii=False, allow_nan=False))
    else:
        unittest.main()
