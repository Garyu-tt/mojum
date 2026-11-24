import unittest

from mojum.kinematics_benchmark import evaluate_ik_samples, PROVENANCE

LIMITS = {"hip": (-1.0, 1.0), "knee": (0.0, 2.0)}
SAMPLES = [
    {"sample_id": "b", "target_m": (1, 0, 0), "achieved_m": (1.003, 0, 0),
     "joints": {"hip": 0.5, "knee": 1.0}, "elapsed_ns": 200, "peak_bytes": 4096},
    {"sample_id": "a", "target_m": (0, 0, 0), "achieved_m": (0, 0.004, 0),
     "joints": {"hip": 1.2, "knee": 1.0}, "elapsed_ns": 100, "peak_bytes": 2048},
]


class KinematicsBenchmarkTests(unittest.TestCase):
    def test_report_and_limit_excursion(self):
        report = evaluate_ik_samples(SAMPLES, LIMITS, rig_revision="rig-r1")
        self.assertEqual(report.sample_count, 2)
        self.assertEqual([s.sample_id for s in report.samples], ["a", "b"])
        self.assertAlmostEqual(report.samples[0].residual_m, 0.004)
        self.assertFalse(report.within_limits)
        self.assertAlmostEqual(report.samples[0].max_limit_excursion_rad, 0.2)
        self.assertEqual(report.provenance, PROVENANCE)

    def test_input_order_does_not_change_digest(self):
        a = evaluate_ik_samples(SAMPLES, LIMITS, rig_revision="rig-r1")
        b = evaluate_ik_samples(list(reversed(SAMPLES)), dict(reversed(list(LIMITS.items()))), rig_revision="rig-r1")
        self.assertEqual(a.digest, b.digest)

    def test_budget_is_advisory_and_reported(self):
        report = evaluate_ik_samples(SAMPLES, LIMITS, rig_revision="rig-r1",
                                     max_elapsed_ns=150, max_peak_bytes=3000)
        self.assertTrue(report.budget_exceeded)
        self.assertEqual(report.max_elapsed_ns, 200)

    def test_valid_boundaries(self):
        samples = [{"sample_id": "x", "target_m": (0, 0, 0), "achieved_m": (0, 0, 0),
                    "joints": {"hip": -1, "knee": 2}, "elapsed_ns": 0, "peak_bytes": 0}]
        report = evaluate_ik_samples(samples, LIMITS, rig_revision="r")
        self.assertTrue(report.within_limits)
        self.assertEqual(report.max_residual_m, 0)

    def test_duplicate_sample_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate_ik_samples(SAMPLES + [dict(SAMPLES[0])], LIMITS, rig_revision="r")

    def test_bool_not_accepted_as_integer(self):
        bad = [dict(SAMPLES[0], elapsed_ns=True)]
        with self.assertRaisesRegex(ValueError, "elapsed_ns"):
            evaluate_ik_samples(bad, LIMITS, rig_revision="r")

    def test_nonfinite_rejected(self):
        bad = [dict(SAMPLES[0], achieved_m=(float("nan"), 0, 0))]
        with self.assertRaises(ValueError):
            evaluate_ik_samples(bad, LIMITS, rig_revision="r")

    def test_extremely_large_integer_rejected_as_coordinate(self):
        bad = [dict(SAMPLES[0], target_m=(10 ** 1000, 0, 0))]
        with self.assertRaises(ValueError):
            evaluate_ik_samples(bad, LIMITS, rig_revision="r")

    def test_joint_mismatch_rejected(self):
        bad = [dict(SAMPLES[0], joints={"hip": 0.0})]
        with self.assertRaisesRegex(ValueError, "joint set"):
            evaluate_ik_samples(bad, LIMITS, rig_revision="r")

    def test_unknown_fields_rejected(self):
        bad = [dict(SAMPLES[0], physical_contact=True)]
        with self.assertRaisesRegex(ValueError, "unknown"):
            evaluate_ik_samples(bad, LIMITS, rig_revision="r")

    def test_provenance_gate(self):
        with self.assertRaisesRegex(ValueError, "PUBLIC_SYNTHETIC"):
            evaluate_ik_samples(SAMPLES, LIMITS, rig_revision="r", provenance="PRIVATE")

    def test_caller_mutation_does_not_change_report(self):
        samples = [dict(SAMPLES[0], joints=dict(SAMPLES[0]["joints"]))]
        report = evaluate_ik_samples(samples, LIMITS, rig_revision="r")
        samples[0]["joints"]["hip"] = 99
        self.assertTrue(report.samples[0].within_limits)

    def test_oversized_collection_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_ik_samples([], LIMITS, rig_revision="r")


if __name__ == "__main__":
    unittest.main()
