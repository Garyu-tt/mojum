"""R0.14: temporal numeric near-field transfer and adverse controls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.temporal_range_benchmark import (
    FORWARD_X_M, SCHEMA, TemporalError, TemporalRangeModel,
    measured_transition, run_temporal_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


class TemporalRangeBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.result = run_temporal_benchmark()

    def test_real_measured_training_and_frozen_nontraining(self):
        self.assertEqual(self.result["schema"], SCHEMA)
        self.assertEqual(self.result["training_transitions"], 3)
        self.assertEqual(self.result["training_range_buckets"], [3, 2, 1])
        self.assertEqual(self.result["frozen_updates"], 0)

    def test_unseen_green_rgb_at_different_absolute_location(self):
        rows = self.result["novel_rgb_and_new_position"]
        self.assertEqual([r["prior_range_bucket"] for r in rows], [3, 2, 1])
        self.assertEqual([r["measured_next_bucket"] for r in rows], [2, 1, 0])
        self.assertEqual([r["learned_next_bucket"] for r in rows], [2, 1, 0])
        self.assertEqual([r["learned_squared_error"] for r in rows], [0, 0, 0])
        self.assertEqual([r["fixed_persistence_squared_error"] for r in rows],
                         [1, 1, 1])
        self.assertTrue(all(r["frozen_model_prediction"] is None for r in rows))

    def test_unseen_initial_range_bucket_reports_no_coverage(self):
        shifted = self.result["novel_initial_distance"]
        self.assertEqual([r["prior_range_bucket"] for r in shifted], [4, 3, 2])
        self.assertEqual([r["measured_next_bucket"] for r in shifted], [3, 2, 1])
        self.assertEqual([r["learned_next_bucket"] for r in shifted], [None, 2, 1])
        self.assertIsNone(shifted[0]["learned_squared_error"])
        self.assertEqual([r["learned_squared_error"] for r in shifted[1:]], [0, 0])

    def test_hidden_wall_is_an_explicit_negative_case(self):
        rows = self.result["unseen_wall_negative_control"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["world_blocked"])
        self.assertEqual(row["prior_range_bucket"], 3)
        self.assertEqual(row["measured_next_bucket"], 3)
        self.assertEqual(row["learned_next_bucket"], 2)
        self.assertEqual(row["learned_squared_error"], 1)
        self.assertEqual(row["fixed_persistence_squared_error"], 0)

    def test_model_can_replay_valid_snapshot_exactly(self):
        model = TemporalRangeModel()
        frames = measured_transition(toy_id="train.a", center_x=1.2,
                                     start_x=.8, rgb=(200, 80, 30))
        for frame in frames:
            before = frame["before"]
            model.learn(before, FORWARD_X_M, frame["after"],
                        model.predict(before, FORWARD_X_M))
        snapshot = model.snapshot()
        recreated = TemporalRangeModel.from_snapshot(snapshot)
        self.assertEqual(recreated.snapshot(), snapshot)
        query = measured_transition(toy_id="heldout", center_x=2.8,
                                    start_x=2.4, rgb=(20, 220, 90))[0]["before"]
        self.assertEqual(recreated.predict(query, FORWARD_X_M),
                         model.predict(query, FORWARD_X_M))
        mutated_copy = recreated.snapshot()
        mutated_copy["transitions"]["3:+"]["2"] = 90
        self.assertEqual(recreated.snapshot(), snapshot)

    def test_snapshot_rejects_invalid_histogram_counts_and_keys(self):
        valid = {"schema": SCHEMA, "updates": 1,
                 "transitions": {"3:+": {"2": 1}}}
        invalid = []
        for key in ("3:outside", "a:+", "9:+", "3:+\x00", "9" * 5000 + ":+"):
            s = copy.deepcopy(valid)
            s["transitions"] = {key: {"2": 1}}
            invalid.append(s)
        for val in (-1, True, 1.5, 200):
            s = copy.deepcopy(valid)
            s["transitions"]["3:+"]["2"] = val
            invalid.append(s)
        s = copy.deepcopy(valid)
        s["updates"] = 2
        invalid.append(s)
        for s in invalid:
            with self.subTest(value=str(s)[:90]), self.assertRaises(TemporalError):
                TemporalRangeModel.from_snapshot(s)

    def test_reject_forged_prior_and_stale_future_sensors_without_mutation(self):
        model = TemporalRangeModel()
        frame = measured_transition(toy_id="valid", center_x=1.2,
                                    start_x=.8, rgb=(200, 80, 30))[0]
        before, after = frame["before"], frame["after"]
        prior = model.predict(before, FORWARD_X_M)
        initial = model.snapshot()
        with self.assertRaises(TemporalError):
            model.learn(before, FORWARD_X_M, after,
                        {"predicted_next_bucket": 0, "observations": 9})
        forged = copy.deepcopy(after)
        forged["tick"] = before["tick"]
        with self.assertRaises(TemporalError):
            model.learn(before, FORWARD_X_M, forged, prior)
        forged = copy.deepcopy(after)
        forged["softness"] = 0.8
        with self.assertRaises(ValueError):
            model.learn(before, FORWARD_X_M, forged, prior)
        self.assertEqual(model.snapshot(), initial)

    def test_rgb_and_spectator_id_are_not_model_features(self):
        model = TemporalRangeModel()
        seen = measured_transition(toy_id="a.private", center_x=1.2,
                                   start_x=.8, rgb=(200, 10, 10))[0]
        model.learn(seen["before"], FORWARD_X_M, seen["after"],
                    model.predict(seen["before"], FORWARD_X_M))
        other = measured_transition(toy_id="b.private", center_x=2.8,
                                    start_x=2.4, rgb=(10, 200, 10))[0]
        self.assertEqual(model.predict(other["before"], FORWARD_X_M),
                         {"predicted_next_bucket": 2, "observations": 1})
        state = json.dumps(model.snapshot(), allow_nan=False)
        self.assertNotIn("200", state)
        self.assertNotIn("private", state)
        self.assertEqual(set(model.snapshot()["transitions"]), {"3:+"})

    def test_unobserved_opposite_motion_is_not_guessed(self):
        first = measured_transition(toy_id="seen", center_x=1.2,
                                    start_x=.8, rgb=(220, 80, 30))[0]
        model = TemporalRangeModel()
        model.learn(first["before"], FORWARD_X_M, first["after"],
                    model.predict(first["before"], FORWARD_X_M))
        self.assertIsNone(model.predict(first["before"], -FORWARD_X_M)[
            "predicted_next_bucket"])

    def test_repeatability_json_and_subprocess_outside_checkout(self):
        self.assertEqual(self.result, run_temporal_benchmark())
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        process = subprocess.run(
            [sys.executable, "-m", "mojum.temporal_range_benchmark"],
            cwd=ROOT / "tests", env=env, capture_output=True,
            text=True, timeout=30, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), self.result)
        json.dumps(self.result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
