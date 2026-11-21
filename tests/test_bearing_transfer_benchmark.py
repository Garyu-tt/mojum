"""Reproducible held-out transfer + honest negative controls for R0.14."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.bearing_transfer_benchmark import (
    ACTION, SCHEMA, STEP_METERS, _measured_case, evaluate,
)
from mojum.directional_outcomes import BearingOutcomeMemory
from mojum.temporal_outcomes import RangeOutcomeMemory
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent


class FrozenDirectionTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate()
        cls.samples = {s["case"]: s for s in cls.report["samples"]}

    def test_reproducible_exact_trial_set_and_frozen_model_counts(self):
        report = self.report
        self.assertEqual(report["schema"], SCHEMA)
        self.assertEqual(report["action"], ACTION)
        self.assertEqual(report["step_m"], STEP_METERS)
        self.assertEqual(report["train_count"], 2)
        self.assertEqual(report["test_count"], 7)
        self.assertEqual(len(self.samples), 7)
        self.assertEqual(evaluate(), report)
        self.assertEqual(report["models"]["bearing"], {
            "correct_pairs": 3, "wrong_pairs": 2,
            "abstained": 2, "answered": 5, "total": 7,
        })
        self.assertEqual(report["models"]["numeric_only"], {
            "correct_pairs": 0, "wrong_pairs": 0,
            "abstained": 7, "answered": 0, "total": 7,
        })
        self.assertEqual(report["models"]["frozen_untrained"],
                         report["models"]["numeric_only"])

    def test_translated_new_room_and_changed_rgb_transfer(self):
        for label, side, future in (
                ("east.translated.new_room", "x+", 1),
                ("west.translated.new_room", "x-", 2),
                ("east.new_rgb_and_material", "x+", 1)):
            with self.subTest(label=label):
                trial = self.samples[label]
                self.assertEqual(trial["initial_bucket"], 2)
                self.assertEqual(trial["observed_bearing"], side)
                self.assertFalse(trial["actual_blocked"])
                self.assertEqual(trial["actual_next_bucket"], future)
                self.assertAlmostEqual(trial["applied_dx"], STEP_METERS)
                self.assertEqual(trial["predictions"]["bearing"], {
                    "observations": 1, "next_bucket": future,
                    "blocked": False,
                })
                self.assertIsNone(trial["predictions"]["numeric_only"]["blocked"])

    def test_wrong_predictions_for_new_distance_and_hidden_wall(self):
        distance = self.samples["east.same_bearing_farther"]
        self.assertEqual(distance["observed_bearing"], "x+")
        self.assertEqual(distance["initial_bucket"], 2)
        self.assertEqual(distance["actual_next_bucket"], 2)
        self.assertFalse(distance["actual_blocked"])
        self.assertEqual(distance["predictions"]["bearing"]["next_bucket"], 1)
        self.assertFalse(distance["predictions"]["bearing"]["blocked"])

        wall = self.samples["west.unseen_wall"]
        self.assertEqual(wall["observed_bearing"], "x-")
        self.assertTrue(wall["actual_blocked"])
        self.assertEqual(wall["applied_dx"], 0.0)
        self.assertEqual(wall["actual_next_bucket"], 2)
        self.assertEqual(wall["predictions"]["bearing"]["next_bucket"], 2)
        self.assertFalse(wall["predictions"]["bearing"]["blocked"])

    def test_unknown_bearing_and_occluded_scene_explicitly_abstain(self):
        for label, bearing in (
                ("north.untrained_bearing", "y+"),
                ("east.occluded", None)):
            with self.subTest(label=label):
                t = self.samples[label]
                self.assertEqual(t["observed_bearing"], bearing)
                self.assertEqual(t["predictions"]["bearing"], {
                    "observations": 0, "next_bucket": None, "blocked": None,
                })
        hidden = self.samples["east.occluded"]
        self.assertIsNone(hidden["initial_bucket"])
        self.assertTrue(hidden["actual_blocked"])

    def test_fixture_measurement_is_an_actual_bounded_movement(self):
        seen = _measured_case("qa.real-move", x=1.22, rgb=(20, 120, 250))
        self.assertEqual(seen["before"]["bearing_bin"], "x+")
        self.assertEqual(seen["before_numeric"]["range_bucket_10cm"], 2)
        self.assertEqual(seen["after"]["range_bucket_10cm"], 1)
        self.assertFalse(seen["blocked"])
        self.assertEqual(seen["requested_dx"], .04)
        self.assertAlmostEqual(seen["applied_dx"], .04)
        self.assertEqual(BearingOutcomeMemory().updates, 0)
        self.assertEqual(RangeOutcomeMemory().updates, 0)
        # Obstacle is outside the movement path: a changed room can still
        # have the same measured transition. Never infer from map metadata.
        altered = _measured_case(
            "qa.remote-wall", x=1.62, y=2.1, start=(1.4, 2.1),
            room=Room(5., 4., ((3., 1., .1, 2.),)),
        )
        self.assertEqual(altered["after"]["range_bucket_10cm"], 1)
        self.assertFalse(altered["blocked"])

    def test_no_spectator_id_or_softness_in_model_predictions(self):
        encoded = json.dumps(self.report, sort_keys=True)
        self.assertNotIn("spectator.fixture.", encoded)
        self.assertNotIn("softness", encoded)
        self.assertNotIn("reflectance_rgb", encoded)
        self.assertEqual(len(self.report["samples"]), 7)
        for sample in self.report["samples"]:
            self.assertEqual(set(sample["predictions"]), {
                "numeric_only", "bearing", "frozen_untrained",
            })
            self.assertLessEqual(abs(sample["applied_dx"]), STEP_METERS + 1e-12)

    def test_offline_cli_works_outside_checkout_and_has_no_database(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "mojum.bearing_transfer_benchmark"],
            cwd=ROOT.parent, env=env, capture_output=True, text=True,
            timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.report)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
