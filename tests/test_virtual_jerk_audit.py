import unittest

from mojum.virtual_jerk_audit import (
    PUBLIC_SYNTHETIC, SCHEMA, VirtualJerkError, audit_joint_jerk,
)


def trajectory(times, positions):
    return {
        "schema": SCHEMA,
        "classification": PUBLIC_SYNTHETIC,
        "consent": True,
        "stream_id": "fixture-cubic",
        "joint_names": ["elbow"],
        "samples": [
            {"time_ns": int(t * 1_000_000_000), "positions": [p]}
            for t, p in zip(times, positions)
        ],
    }


class VirtualJerkAuditTests(unittest.TestCase):
    def test_cubic_position_has_constant_jerk(self):
        times = [0, 1, 2, 3, 4, 5]
        report = audit_joint_jerk(trajectory(times, [t ** 3 for t in times]), {"elbow": 6.0})
        self.assertTrue(report.passed)
        self.assertAlmostEqual(report.peak_abs_jerk[0][1], 6.0, places=9)
        self.assertEqual(report.jerk_sample_count, 3)

    def test_nonuniform_time_grid_is_supported(self):
        times = [0, 0.5, 1.5, 3.0, 5.0, 8.0]
        report = audit_joint_jerk(trajectory(times, [t ** 3 for t in times]), {"elbow": 6.0})
        self.assertTrue(report.passed)
        self.assertAlmostEqual(report.peak_abs_jerk[0][1], 6.0, places=8)

    def test_threshold_violation_is_advisory_result(self):
        times = [0, 1, 2, 3, 4]
        report = audit_joint_jerk(trajectory(times, [t ** 3 for t in times]), {"elbow": 5.9})
        self.assertFalse(report.passed)
        self.assertEqual(report.violations[0].joint, "elbow")
        self.assertGreater(report.violations[0].jerk, 5.9)

    def test_digest_is_deterministic_and_input_is_not_mutated(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        before = repr(data)
        one = audit_joint_jerk(data, {"elbow": 10.0})
        two = audit_joint_jerk(data, {"elbow": 10.0})
        self.assertEqual(one.digest, two.digest)
        self.assertEqual(repr(data), before)
        self.assertEqual(len(one.digest), 64)

    def test_requires_four_samples(self):
        data = trajectory([0, 1, 2], [0, 1, 8])
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_duplicate_joint_names(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["joint_names"] = ["elbow", "elbow"]
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_boolean_position(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["samples"][2]["positions"][0] = True
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_non_monotonic_time(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["samples"][3]["time_ns"] = data["samples"][2]["time_ns"]
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_missing_consent(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["consent"] = False
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_bool_limit(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": True})

    def test_rejects_nonfinite_position(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["samples"][2]["positions"][0] = float("nan")
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})

    def test_rejects_unknown_fields(self):
        data = trajectory([0, 1, 2, 3, 4], [0, 1, 8, 27, 64])
        data["unexpected"] = "value"
        with self.assertRaises(VirtualJerkError):
            audit_joint_jerk(data, {"elbow": 10.0})


if __name__ == "__main__":
    unittest.main()
