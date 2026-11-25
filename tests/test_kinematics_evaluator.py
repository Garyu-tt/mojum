import math
import unittest

from mojum.kinematics_evaluator import evaluate_ik_samples, PROVENANCE

LIMITS = {"hip": (-1.0, 1.0), "knee": (0.0, 2.0)}
SAMPLES = [
    {"sample_id": "b", "target_m": (1, 0, 0), "achieved_m": (1.003, 0, 0),
     "joints": {"hip": 0.5, "knee": 1.0}, "elapsed_ns": 200, "peak_bytes": 4096},
    {"sample_id": "a", "target_m": (0, 0, 0), "achieved_m": (0, 0.004, 0),
     "joints": {"hip": 1.2, "knee": 1.0}, "elapsed_ns": 100, "peak_bytes": 2048},
]


class KinematicsEvaluatorTests(unittest.TestCase):
    @staticmethod
    def _single_joint_report(position, bounds, *, target=(0, 0, 0), achieved=(0, 0, 0)):
        sample = {
            "sample_id": "numeric-probe", "target_m": target, "achieved_m": achieved,
            "joints": {"joint": position}, "elapsed_ns": 1, "peak_bytes": 1,
        }
        return evaluate_ik_samples([sample], {"joint": bounds}, rig_revision="numeric-rig")

    def test_report_and_limit_excursion(self):
        report = evaluate_ik_samples(SAMPLES, LIMITS, rig_revision="rig-r1")
        self.assertEqual(report.sample_count, 2)
        self.assertEqual([s.sample_id for s in report.samples], ["a", "b"])
        self.assertAlmostEqual(report.samples[0].residual_m, 0.004)
        self.assertFalse(report.within_limits)
        self.assertAlmostEqual(report.samples[0].max_limit_excursion_rad, 0.2)
        self.assertEqual(report.provenance, PROVENANCE)
        self.assertEqual(report.schema, "mojum.kinematics-evaluation/1")

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

    def test_mean_residual_handles_multiple_large_finite_values(self):
        huge = 1e308
        samples = [
            {"sample_id": "a", "target_m": (0.0, 0.0, 0.0), "achieved_m": (huge, 0.0, 0.0),
             "joints": {"hip": 0.0, "knee": 1.0}, "elapsed_ns": 1, "peak_bytes": 1},
            {"sample_id": "b", "target_m": (0.0, 0.0, 0.0), "achieved_m": (huge, 0.0, 0.0),
             "joints": {"hip": 0.0, "knee": 1.0}, "elapsed_ns": 1, "peak_bytes": 1},
        ]
        report = evaluate_ik_samples(samples, LIMITS, rig_revision="r")
        self.assertEqual(report.max_residual_m, huge)
        self.assertEqual(report.mean_residual_m, huge)

    def test_rejects_lossy_integer_joint_bounds(self):
        boundary = 2**53
        for lower, upper, position in (
            (boundary + 1, boundary + 2, boundary),
            (boundary, boundary + 1, boundary),
            (-boundary - 2, -boundary - 1, -boundary),
            (-boundary - 1, -boundary, -boundary),
        ):
            with self.subTest(lower=lower, upper=upper):
                with self.assertRaisesRegex(ValueError, "joint limits.*lossless"):
                    self._single_joint_report(position, (lower, upper))

    def test_rejects_lossy_integer_joint_positions(self):
        boundary = 2**53
        for position in (boundary + 1, -boundary - 1):
            with self.subTest(position=position):
                with self.assertRaisesRegex(ValueError, "joint positions.*lossless"):
                    self._single_joint_report(position, (-2 * boundary, 2 * boundary))

    def test_rejects_lossy_integer_coordinates(self):
        boundary = 2**53
        for sign in (-1, 1):
            for field in ("target", "achieved"):
                for axis in range(3):
                    values = {"target": [0, 0, 0], "achieved": [0, 0, 0]}
                    values["target"][axis] = sign * boundary
                    values["achieved"][axis] = sign * boundary
                    values[field][axis] = sign * (boundary + 1)
                    with self.subTest(sign=sign, field=field, axis=axis):
                        with self.assertRaisesRegex(ValueError, "lossless integer conversion"):
                            self._single_joint_report(0, (-1, 1), **values)

    def test_exact_large_integer_inputs_remain_supported(self):
        boundary = 2**53
        for magnitude in (boundary - 1, boundary, boundary + 2, 2**100, 2**1023):
            for sign in (-1, 1):
                with self.subTest(magnitude=magnitude, sign=sign):
                    report = self._single_joint_report(
                        sign * magnitude, (-magnitude, magnitude),
                        target=(sign * magnitude, 0, 0),
                    )
                    self.assertTrue(report.within_limits)
                    self.assertEqual(report.max_residual_m, float(magnitude))
                    self.assertEqual(report.samples[0].max_limit_excursion_rad, 0.0)

    def test_exact_out_of_limit_integers_preserve_nonzero_results(self):
        boundary = 2**53
        for sign, bounds in (
            (1, (boundary + 2, boundary + 4)),
            (-1, (-boundary - 4, -boundary - 2)),
        ):
            with self.subTest(sign=sign):
                report = self._single_joint_report(
                    sign * boundary, bounds,
                    target=(sign * (boundary + 2), 0, 0),
                    achieved=(sign * boundary, 0, 0),
                )
                self.assertFalse(report.within_limits)
                self.assertEqual(report.max_residual_m, 2.0)
                self.assertEqual(report.samples[0].max_limit_excursion_rad, 2.0)

    def test_wide_finite_float_limits_keep_endpoints_compliant(self):
        for position in (-1e308, 0.0, 1e308):
            with self.subTest(position=position):
                report = self._single_joint_report(position, (-1e308, 1e308))
                self.assertTrue(report.within_limits)
                self.assertEqual(report.samples[0].max_limit_excursion_rad, 0.0)

    def test_rejects_nonfinite_derived_joint_excursions(self):
        for position, bounds in (
            (-1e308, (1e308, 1.5e308)),
            (1e308, (-1.5e308, -1e308)),
            (-2**1023, (2**1023, 2**1023 + 2**971)),
        ):
            with self.subTest(position=position, bounds=bounds):
                with self.assertRaisesRegex(ValueError, "joint limit excursion"):
                    self._single_joint_report(position, bounds)

    def test_subnormal_residual_and_excursion_remain_nonzero(self):
        tiny = math.ulp(0.0)
        for sign, bounds in ((1, (tiny, 2 * tiny)), (-1, (-2 * tiny, -tiny))):
            with self.subTest(sign=sign):
                report = self._single_joint_report(0.0, bounds, target=(sign * tiny, 0, 0))
                self.assertFalse(report.within_limits)
                self.assertEqual(report.max_residual_m, tiny)
                self.assertEqual(report.samples[0].max_limit_excursion_rad, tiny)

    def test_rejects_nonfinite_derived_coordinate_residual(self):
        with self.assertRaisesRegex(ValueError, "coordinate residual"):
            self._single_joint_report(
                0.0, (-1.0, 1.0), target=(1e308, 0, 0), achieved=(-1e308, 0, 0),
            )

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


    def test_hostile_string_subclass_keys_rejected_before_hashing(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            def __eq__(self, other):
                raise AssertionError("untrusted key equality must not execute")

        bad_sample = dict(SAMPLES[0])
        bad_sample[HostileKey("sample_id")] = bad_sample.pop("sample_id")
        with self.assertRaisesRegex(ValueError, "native strings"):
            evaluate_ik_samples([bad_sample], LIMITS, rig_revision="r")

        bad_joint_row = dict(SAMPLES[0])
        bad_joints = dict(bad_joint_row["joints"])
        bad_joints[HostileKey("hip")] = bad_joints.pop("hip")
        bad_joint_row["joints"] = bad_joints
        with self.assertRaisesRegex(ValueError, "native strings"):
            evaluate_ik_samples([bad_joint_row], LIMITS, rig_revision="r")

    def test_oversized_native_keys_rejected_before_hashing(self):
        bad_sample = dict(SAMPLES[0])
        bad_sample["x" * 1_000_000] = bad_sample.pop("sample_id")
        with self.assertRaisesRegex(ValueError, "bounded native strings"):
            evaluate_ik_samples([bad_sample], LIMITS, rig_revision="r")

        bad_joint_row = dict(SAMPLES[0])
        bad_joints = dict(bad_joint_row["joints"])
        bad_joints["x" * 1_000_000] = bad_joints.pop("hip")
        bad_joint_row["joints"] = bad_joints
        with self.assertRaisesRegex(ValueError, "bounded native strings"):
            evaluate_ik_samples([bad_joint_row], LIMITS, rig_revision="r")

    def test_oversized_mappings_rejected_before_key_inspection(self):
        oversized_sample = dict(SAMPLES[0])
        oversized_sample.update({f"extra-{i}": i for i in range(10_000)})
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            evaluate_ik_samples([oversized_sample], LIMITS, rig_revision="r")

        oversized_joints = dict(SAMPLES[0]["joints"])
        oversized_joints.update({f"extra-{i}": i for i in range(10_000)})
        oversized_row = dict(SAMPLES[0], joints=oversized_joints)
        with self.assertRaisesRegex(ValueError, "joint set"):
            evaluate_ik_samples([oversized_row], LIMITS, rig_revision="r")

    def test_oversized_collection_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_ik_samples([], LIMITS, rig_revision="r")


if __name__ == "__main__":
    unittest.main()
