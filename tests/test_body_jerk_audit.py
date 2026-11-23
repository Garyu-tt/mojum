import unittest
from dataclasses import FrozenInstanceError

from mojum.body_jerk_audit import BodyJerkError, audit_joint_jerk, MAX_TIMESTAMP_NS


def trajectory(times=(0, 1_000_000_000, 2_000_000_000, 3_000_000_000),
              positions=(0.0, 1.0, 8.0, 27.0)):
    return {
        "schema": "mojum.body.joint-trajectory/1",
        "classification": "PUBLIC_SYNTHETIC",
        "consent": True,
        "stream_id": "fixture-jerk-01",
        "joint_names": ["elbow"],
        "samples": [
            {"time_ns": timestamp, "positions": [position]}
            for timestamp, position in zip(times, positions)
        ],
    }


class HostileString(str):
    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise AssertionError("hostile equality must not run")


class DictSubclass(dict):
    pass


class ListSubclass(list):
    pass


class BodyJerkAuditTests(unittest.TestCase):
    def audit(self, value=None, limits=None):
        return audit_joint_jerk(
            trajectory() if value is None else value,
            {"elbow": 10.0} if limits is None else limits,
        )

    def test_cubic_position_has_exact_constant_jerk(self):
        report = self.audit()
        self.assertTrue(report.passed)
        self.assertAlmostEqual(report.peak_abs_jerk[0][1], 6.0)
        self.assertEqual(report.window_count, 1)

    def test_nonuniform_timestamps_preserve_cubic_jerk(self):
        # q=t^3 for t=[0,1,3,6] seconds.
        value = trajectory(
            times=(0, 1_000_000_000, 3_000_000_000, 6_000_000_000),
            positions=(0.0, 1.0, 27.0, 216.0),
        )
        report = self.audit(value)
        self.assertAlmostEqual(report.peak_abs_jerk[0][1], 6.0, places=10)

    def test_threshold_crossing_is_reported(self):
        report = self.audit(limits={"elbow": 5.5})
        self.assertFalse(report.passed)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].joint, "elbow")
        self.assertEqual(report.violations[0].window_start_index, 0)
        self.assertAlmostEqual(report.violations[0].jerk, 6.0)

    def test_negative_jerk_is_checked_by_absolute_magnitude(self):
        value = trajectory(positions=(0.0, -1.0, -8.0, -27.0))
        report = self.audit(value, {"elbow": 5.5})
        self.assertFalse(report.passed)
        self.assertAlmostEqual(report.violations[0].jerk, -6.0)

    def test_multiple_windows_report_peak_and_each_violation(self):
        value = trajectory(
            times=(0, 1_000_000_000, 2_000_000_000, 3_000_000_000, 4_000_000_000),
            positions=(0.0, 1.0, 8.0, 27.0, 64.0),
        )
        report = self.audit(value)
        self.assertEqual(report.window_count, 2)
        self.assertAlmostEqual(report.peak_abs_jerk[0][1], 6.0)
        self.assertTrue(report.passed)

    def test_multiple_joints_keep_limits_and_peaks_separate(self):
        value = trajectory()
        value["joint_names"] = ["elbow", "wrist"]
        for sample in value["samples"]:
            sample["positions"].append(0.0)
        report = self.audit(value, {"elbow": 5.0, "wrist": 0.1})
        self.assertEqual(report.peak_abs_jerk[0][0], "elbow")
        self.assertEqual(report.peak_abs_jerk[1], ("wrist", 0.0))
        self.assertEqual([item.joint for item in report.violations], ["elbow"])

    def test_digest_is_deterministic_and_domain_separated(self):
        first = self.audit()
        second = self.audit()
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(len(first.digest), 64)
        self.assertNotEqual(first.digest, "0" * 64)

    def test_digest_changes_when_sample_changes(self):
        first = self.audit()
        second = self.audit(trajectory(positions=(0.0, 1.0, 8.0, 28.0)))
        self.assertNotEqual(first.digest, second.digest)

    def test_report_is_detached_and_immutable(self):
        value = trajectory()
        report = self.audit(value)
        value["samples"][1]["positions"][0] = 500.0
        self.assertEqual(report.sample_count, 4)
        with self.assertRaises(FrozenInstanceError):
            report.stream_id = "changed"

    def test_timestamp_subtraction_retains_ns_resolution_near_int64_max(self):
        base = MAX_TIMESTAMP_NS - 3
        value = trajectory(
            times=(base, base + 1, base + 2, base + 3),
            positions=(0.0, 0.0, 0.0, 1.0),
        )
        report = self.audit(value, {"elbow": 1.0e15})
        self.assertEqual(report.window_count, 1)
        self.assertTrue(report.peak_abs_jerk[0][1] > 0.0)

    def test_rejects_wrong_schema(self):
        value = trajectory()
        value["schema"] = "mojum.body.joint-trajectory/2"
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_non_public_classification(self):
        value = trajectory()
        value["classification"] = "PRIVATE"
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_requires_exact_true_consent(self):
        for consent in (False, 1, "true"):
            value = trajectory()
            value["consent"] = consent
            with self.subTest(consent=consent), self.assertRaises(BodyJerkError):
                self.audit(value)

    def test_rejects_boolean_and_nonfinite_positions(self):
        for bad in (True, float("nan"), float("inf"), float("-inf")):
            value = trajectory()
            value["samples"][1]["positions"][0] = bad
            with self.subTest(value=bad), self.assertRaises(BodyJerkError):
                self.audit(value)

    def test_rejects_boolean_timestamp(self):
        value = trajectory()
        value["samples"][1]["time_ns"] = True
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_duplicate_or_regressing_timestamps(self):
        for times in (
            (0, 0, 2_000_000_000, 3_000_000_000),
            (0, 2_000_000_000, 1_000_000_000, 3_000_000_000),
        ):
            with self.subTest(times=times), self.assertRaises(BodyJerkError):
                self.audit(trajectory(times=times))

    def test_rejects_timestamp_outside_signed_64_bit_range(self):
        value = trajectory(times=(0, 1, 2, 1 << 63))
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_position_dimension_mismatch(self):
        value = trajectory()
        value["samples"][2]["positions"] = []
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_duplicate_joint_names(self):
        value = trajectory()
        value["joint_names"] = ["elbow", "elbow"]
        with self.assertRaises(BodyJerkError):
            self.audit(value, {"elbow": 1.0})

    def test_rejects_missing_and_extra_limit_names(self):
        for limits in ({"other": 1.0}, {"elbow": 1.0, "wrist": 1.0}):
            with self.subTest(limits=limits), self.assertRaises(BodyJerkError):
                self.audit(limits=limits)

    def test_rejects_oversized_limit_mapping_before_key_scan(self):
        limits = {f"unused-{index}": 1.0 for index in range(50_000)}
        with self.assertRaisesRegex(BodyJerkError, "missing or unknown fields"):
            self.audit(limits=limits)

    def test_rejects_boolean_or_nonpositive_limit(self):
        for limit in (True, 0.0, -1.0):
            with self.subTest(limit=limit), self.assertRaises(BodyJerkError):
                self.audit(limits={"elbow": limit})

    def test_rejects_mapping_subclasses(self):
        value = DictSubclass(trajectory())
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_hostile_string_subclass_keys_before_equality(self):
        value = trajectory()
        value[HostileString("extra")] = None
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_list_subclasses(self):
        value = trajectory()
        value["joint_names"] = ListSubclass(value["joint_names"])
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_total_position_value_budget(self):
        value = trajectory()
        value["joint_names"] = [f"joint-{index:03d}" for index in range(256)]
        value["samples"] = [
            {"time_ns": index, "positions": [0.0] * 256}
            for index in range(257)
        ]
        with self.assertRaises(BodyJerkError):
            self.audit(value, {name: 1.0 for name in value["joint_names"]})

    def test_rejects_too_few_samples(self):
        value = trajectory()
        value["samples"] = value["samples"][:3]
        with self.assertRaises(BodyJerkError):
            self.audit(value)

    def test_rejects_unknown_top_level_fields(self):
        value = trajectory()
        value["unreviewed_payload"] = {"secret": "must-not-be-echoed"}
        with self.assertRaises(BodyJerkError):
            self.audit(value)


if __name__ == "__main__":
    unittest.main()
