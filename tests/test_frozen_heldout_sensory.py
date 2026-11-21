"""R0.13 controlled learning-vs-frozen and held-out *negative* controls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.sensory_benchmark import (
    FROZEN_FALLBACK, SCHEMA, Trial, _measure, run_benchmark,
)
from mojum.sensory_play import ContactAssociations


ROOT = Path(__file__).resolve().parent.parent


class FrozenHeldOutSensoryTests(unittest.TestCase):
    def setUp(self):
        self.result = run_benchmark()
        self.rows = {x["group"]: x for x in self.result["heldout_trials"]}

    def test_blank_at_birth_then_actual_measured_training(self):
        self.assertEqual(self.result["schema"], SCHEMA)
        self.assertEqual(self.result["initial_predictions"], [None, None])
        self.assertEqual(self.result["training_probes"], 4)
        self.assertEqual(self.result["learned_model_updates"], 4)
        self.assertEqual(self.result["frozen_model_updates"], 0)

    def test_same_numeric_rgb_new_toy_id_predicts_from_experience(self):
        for group, expected in (
            ("new_instance_same_red", .75),
            ("new_instance_same_blue", .20),
        ):
            with self.subTest(group=group):
                row = self.rows[group]
                self.assertEqual(row["learned_prediction"], expected)
                self.assertTrue(row["learned_coverage"])
                self.assertEqual(row["learned_squared_error"], 0)
                self.assertGreater(row["frozen_default_squared_error"], 0)

    def test_frozen_control_never_learns_and_has_no_native_prediction(self):
        for row in self.result["heldout_trials"]:
            self.assertIsNone(row["frozen_model_prediction"])
            self.assertEqual(row["frozen_default_prior"], FROZEN_FALLBACK)
            observed = row["measured_contact_deflection"]
            self.assertAlmostEqual(
                row["frozen_default_squared_error"],
                (FROZEN_FALLBACK - observed) ** 2,
            )

    def test_contradictory_hidden_material_is_a_negative_transfer(self):
        row = self.rows["contradictory_red"]
        self.assertEqual(row["cue_rgb"], self.rows["new_instance_same_red"]["cue_rgb"])
        self.assertEqual(row["learned_prediction"], .75)
        self.assertEqual(row["measured_contact_deflection"], .10)
        self.assertGreater(row["learned_squared_error"],
                           row["frozen_default_squared_error"])
        self.assertAlmostEqual(row["learned_squared_error"], .4225)

    def test_new_rgb_is_uncovered_not_falsely_predicted(self):
        novel = self.rows["new_unseen_green"]
        self.assertFalse(novel["learned_coverage"])
        self.assertIsNone(novel["learned_prediction"])
        self.assertIsNone(novel["learned_squared_error"])
        self.assertEqual(novel["measured_contact_deflection"], .40)

    def test_benchmark_is_deterministic_and_finite_json(self):
        a = self.result
        b = run_benchmark()
        self.assertEqual(a, b)
        self.assertEqual(json.loads(json.dumps(a, allow_nan=False)), b)
        self.assertEqual(len(a["heldout_trials"]), 4)

    def test_probe_comes_from_scene_and_not_predicted_material_label(self):
        observation, instrument = _measure(Trial(
            "spectator.fixture", (200, 70, 30), .35,
        ))
        self.assertEqual(set(observation), {
            "schema", "tick", "world_revision", "visible",
            "reflectance_rgb", "range_bucket_10cm", "source",
        })
        self.assertNotIn("softness", observation)
        self.assertNotIn("spectator.fixture", str(observation))
        self.assertTrue(instrument["contact"])
        self.assertEqual(instrument["measured_deflection"], .35)
        blank = ContactAssociations()
        self.assertIsNone(blank.predict(observation)["predicted_deflection"])
        self.assertEqual(blank.snapshot()["updates"], 0)

    def test_benchmark_module_runs_without_repo_cwd(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "mojum.sensory_benchmark"],
            cwd=ROOT / "tests", env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual(data, self.result)


if __name__ == "__main__":
    unittest.main()
