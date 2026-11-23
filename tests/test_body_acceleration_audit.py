import unittest
from unittest.mock import patch
from dataclasses import FrozenInstanceError

from mojum.body_acceleration_audit import (
    BodyAccelerationError,
    audit_joint_acceleration,
)


def trajectory(times=(0, 1_000_000_000, 2_000_000_000), positions=(0.0, 1.0, 2.0)):
    return {
        "schema": "mojum.body.joint-trajectory/1",
        "classification": "PUBLIC_SYNTHETIC",
        "consent": True,
        "stream_id": "fixture-01",
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


class BodyAccelerationAuditTests(unittest.TestCase):
    def audit(self, value=None, limits=None):
        return audit_joint_acceleration(
            trajectory() if value is None else value,
            {"elbow": 10.0} if limits is None else limits,
        )

    def test_constant_velocity_has_zero_acceleration(self):
        report = self.audit()
        self.assertTrue(report.passed)
        self.assertEqual(report.peak_abs_acceleration, (("elbow", 0.0),))
        self.assertEqual(report.interval_count, 1)

    def test_uniform_quadratic_trajectory_reports_exact_acceleration(self):
        report = self.audit(trajectory(positions=(0.0, 1.0, 4.0)))
        self.assertAlmostEqual(report.peak_abs_acceleration[0][1], 2.0)
        self.assertTrue(report.passed)

    def test_nonuniform_timestamps_use_three_point_formula(self):
        report = self.audit(trajectory(
            times=(0, 1_000_000_000, 3_000_000_000),
            positions=(0.0, 1.0, 9.0),
        ))
        self.assertAlmostEqual(report.peak_abs_acceleration[0][1], 2.0)

    def test_threshold_crossing_is_reported(self):
        report = self.audit(trajectory(positions=(0.0, 1.0, 4.0)), {"elbow": 1.5})
        self.assertFalse(report.passed)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].joint, "elbow")
        self.assertEqual(report.violations[0].middle_sample_index, 1)
        self.assertAlmostEqual(report.violations[0].acceleration, 2.0)

    def test_negative_acceleration_is_measured_by_absolute_magnitude(self):
        report = self.audit(trajectory(positions=(0.0, -1.0, -4.0)), {"elbow": 1.5})
        self.assertFalse(report.passed)
        self.assertAlmostEqual(report.violations[0].acceleration, -2.0)

    def test_multiple_joints_keep_limits_and_peaks_separate(self):
        value = trajectory(positions=(0.0, 1.0, 4.0))
        value["joint_names"] = ["elbow", "wrist"]
        for sample in value["samples"]:
            sample["positions"].append(0.0)
        report = self.audit(value, {"elbow": 1.0, "wrist": 0.01})
        self.assertEqual(report.peak_abs_acceleration[0][0], "elbow")
        self.assertEqual(report.peak_abs_acceleration[1], ("wrist", 0.0))
        self.assertEqual([item.joint for item in report.violations], ["elbow"])

    def test_digest_is_deterministic(self):
        self.assertEqual(self.audit().digest, self.audit().digest)
        self.assertEqual(len(self.audit().digest), 64)

    def test_digest_changes_when_sample_changes(self):
        first = self.audit()
        second = self.audit(trajectory(positions=(0.0, 1.0, 3.0)))
        self.assertNotEqual(first.digest, second.digest)

    def test_report_is_detached_immutable_data(self):
        value = trajectory()
        report = self.audit(value)
        value["samples"][1]["positions"][0] = 500.0
        self.assertEqual(report.sample_count, 3)
        with self.assertRaises(FrozenInstanceError):
            report.stream_id = "changed"

    def test_oversized_fixed_schema_key_rejected_before_set_hashing(self):
        value = trajectory()
        value.pop("schema")
        value["s" * 100_000] = "mojum.body.joint-trajectory/1"
        with patch("mojum.body_acceleration_audit.set", side_effect=AssertionError(
            "oversized schema key reached set hashing"
        ), create=True) as key_set:
            with self.assertRaisesRegex(BodyAccelerationError, "bounded native strings"):
                self.audit(value)
            key_set.assert_not_called()

    def test_rejects_wrong_schema(self):
        value = trajectory()
        value["schema"] = "mojum.body.joint-trajectory/2"
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_non_public_classification(self):
        value = trajectory()
        value["classification"] = "PRIVATE"
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_requires_explicit_native_boolean_consent(self):
        value = trajectory()
        value["consent"] = 1
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_boolean_position(self):
        value = trajectory()
        value["samples"][0]["positions"][0] = True
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_nonfinite_positions(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            value = trajectory()
            value["samples"][1]["positions"][0] = bad
            with self.subTest(value=bad), self.assertRaises(BodyAccelerationError):
                self.audit(value)

    def test_rejects_boolean_timestamp(self):
        value = trajectory()
        value["samples"][1]["time_ns"] = True
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_duplicate_or_regressing_timestamps(self):
        for times in (
            (0, 0, 2_000_000_000),
            (0, 2_000_000_000, 1_000_000_000),
        ):
            with self.subTest(times=times), self.assertRaises(BodyAccelerationError):
                self.audit(trajectory(times=times))

    def test_rejects_timestamp_outside_signed_64_bit_range(self):
        with self.assertRaises(BodyAccelerationError):
            self.audit(trajectory(times=(0, 1, 1 << 63)))

    def test_rejects_position_dimension_mismatch(self):
        value = trajectory()
        value["samples"][1]["positions"] = []
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_duplicate_joint_names(self):
        value = trajectory()
        value["joint_names"] = ["elbow", "elbow"]
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_missing_and_extra_limit_names(self):
        for limits in ({"other": 1.0}, {"elbow": 1.0, "wrist": 1.0}):
            with self.subTest(limits=limits), self.assertRaises(BodyAccelerationError):
                self.audit(limits=limits)

    def test_rejects_oversized_limit_mapping_before_key_scan(self):
        limits = {f"unused-{index}": 1.0 for index in range(50_000)}
        with self.assertRaisesRegex(BodyAccelerationError, "missing or unknown fields"):
            self.audit(limits=limits)

    def test_rejects_boolean_or_nonpositive_limit(self):
        for limit in (True, 0.0, -1.0):
            with self.subTest(limit=limit), self.assertRaises(BodyAccelerationError):
                self.audit(limits={"elbow": limit})

    def test_rejects_mapping_subclasses(self):
        value = DictSubclass(trajectory())
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_hostile_string_subclass_keys_before_equality(self):
        value = trajectory()
        value[HostileString("untrusted")] = None
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_list_subclasses(self):
        value = trajectory()
        value["joint_names"] = ListSubclass(value["joint_names"])
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_total_position_value_budget(self):
        value = trajectory()
        value["joint_names"] = ["joint-%03d" % index for index in range(256)]
        value["samples"] = [
            {"time_ns": index, "positions": [0.0] * 256}
            for index in range(257)
        ]
        with self.assertRaises(BodyAccelerationError):
            self.audit(value, {name: 1.0 for name in value["joint_names"]})

    def test_rejects_too_few_samples(self):
        value = trajectory()
        value["samples"] = value["samples"][:2]
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)

    def test_rejects_unknown_top_level_fields(self):
        value = trajectory()
        value["unreviewed_payload"] = {"secret": "must-not-be-echoed"}
        with self.assertRaises(BodyAccelerationError):
            self.audit(value)


if __name__ == "__main__":
    unittest.main()
