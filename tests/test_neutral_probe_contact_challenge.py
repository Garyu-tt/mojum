"""Adversarial near-contact challenge discovered after the held-out baseline.

These 5 fixtures are *post hoc*, deliberately engineered to force the first
motor intent into a wall. They are NOT called held-out generalization evidence.
"""
from __future__ import annotations

import json
import sys
import unittest

from mojum.neutral_probe_trial import compare
from mojum.world_room import Room

# Every scene begins less than 4 cm from an obstacle immediately to the
# right. No scene geometry is available to a policy: only to world.advance().
SCENES = (
    ("wall-left-face", Room(.64, .48, ((.28, .08, .08, .32),)), (.245, .24)),
    ("narrow-passage-face", Room(.8, .4, ((.34, .12, .12, .28),)),
     (.305, .20)),
    ("maze-wall-one", Room(1.2, .9,
                           ((.4, 0., .08, .65), (.8, .25, .08, .65))),
     (.365, .30)),
    ("maze-wall-two", Room(1.2, .9,
                           ((.4, 0., .08, .65), (.8, .25, .08, .65))),
     (.765, .35)),
    ("room-edge", Room(1.2, .8), (1.2, .40)),
)
TICKS = 120
PULSES = (
    ("alternating", tuple(i % 2 == 0 for i in range(TICKS))),
    ("all-true", (True,) * TICKS),
    ("phase-seven", tuple(i % 7 in (0, 1, 4) for i in range(TICKS))),
)
ARMS = ("adaptive", "frozen", "local_cell")


def challenge():
    for name, room, start in SCENES:
        for pulse_name, pulses in PULSES:
            result = compare(room=room, start=start, pulses=pulses)
            yield name, pulse_name, result


def report():
    rows = []
    for name, pulse, result in challenge():
        rows.append({
            "scene": name,
            "pulse": pulse,
            "arms": {
                arm: {
                    "first_action": result[arm]["trace"][0]["action"],
                    "first_blocked": result[arm]["trace"][0]["blocked"],
                    "attempted": result[arm]["attempted_movement_count"],
                    "successful": result[arm]["successful_movement_count"],
                    "blocked": result[arm]["blocked_count"],
                    "rest": result[arm]["rest_count"],
                    "stalled": result[arm]["stalled_warning"],
                }
                for arm in ARMS
            },
        })
    return {
        "schema": "mojum.research.posthoc-contact-challenge/1",
        "classification": "ADVERSARIAL_POSTHOC_NOT_HELDOUT",
        "cases": len(rows),
        "adaptive_stalls": sum(r["arms"]["adaptive"]["stalled"] for r in rows),
        "local_cell_stalls": sum(r["arms"]["local_cell"]["stalled"] for r in rows),
        "matrix": rows,
    }


class ContactChallengeTests(unittest.TestCase):
    def test_all_initial_measured_contacts_and_no_oracle_action(self):
        for name, pulse, result in challenge():
            with self.subTest(scene=name, pulse=pulse):
                # All controls see same first pulse. None is told about the
                # wall until world.advance measures the attempted outcome.
                for arm in ARMS:
                    first = result[arm]["trace"][0]
                    self.assertTrue(first["heartbeat_pulse"])
                    self.assertEqual(first["action"], "x+")
                    self.assertTrue(first["blocked"], "fixture did not cause real contact")
                    self.assertEqual(first["requested_delta_xy"], [.04, 0.])
                    self.assertGreater(result[arm]["blocked_count"], 0)
                    self.assertEqual(
                        result[arm]["attempted_movement_count"],
                        result[arm]["successful_movement_count"] +
                        result[arm]["blocked_count"])

    def test_aggregate_disclosure_stays_posthoc_and_neutral(self):
        summary = report()
        self.assertEqual(summary["cases"], len(SCENES) * len(PULSES))
        self.assertEqual(summary["classification"],
                         "ADVERSARIAL_POSTHOC_NOT_HELDOUT")
        wire = json.dumps(summary, sort_keys=True, allow_nan=False)
        self.assertEqual(json.loads(wire), summary)
        for secret in ('"trace"', '"learned_state"', '"obstacles"',
                       '"reward"', '"pain"', '"valence"'):
            self.assertNotIn(secret, wire)


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        print(json.dumps(report(), sort_keys=True, allow_nan=False))
    else:
        unittest.main()
