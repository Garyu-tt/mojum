import unittest
from mojum.joint_limits import audit_joint_limits, SCHEMA

class JointLimitAuditTests(unittest.TestCase):
    def setUp(self):
        self.limits = [("hip", -1.0, 1.0), ("knee", 0.0, 2.0)]
        self.samples = [(4, "rig-a", {"hip": -1.0, "knee": 0.5}),
                        (5, "rig-a", {"hip": 0.25, "knee": 2.0})]

    def test_valid_inclusive_boundaries_and_summary(self):
        report = audit_joint_limits(self.limits, self.samples, rig_revision="rig-a")
        self.assertEqual(report.schema, SCHEMA)
        self.assertEqual(report.sample_count, 2)
        self.assertEqual(report.joint_ids, ("hip", "knee"))
        self.assertEqual(report.minimum_observed, (("hip", -1.0), ("knee", 0.5)))
        self.assertEqual(report.maximum_observed, (("hip", 0.25), ("knee", 2.0)))
        self.assertEqual(len(report.digest), 64)

    def test_deterministic_independent_of_limit_order(self):
        a = audit_joint_limits(self.limits, self.samples, rig_revision="rig-a")
        b = audit_joint_limits(list(reversed(self.limits)), self.samples, rig_revision="rig-a")
        self.assertEqual(a, b)

    def test_outside_lower_or_upper_rejected(self):
        for value in (-1.00001, 1.00001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_joint_limits(self.limits, [(0, "rig-a", {"hip": value, "knee": 1.0})],
                                   rig_revision="rig-a")

    def test_bool_and_nonfinite_rejected(self):
        for value in (True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_joint_limits(self.limits, [(0, "rig-a", {"hip": value, "knee": 1.0})],
                                   rig_revision="rig-a")

    def test_duplicate_or_invalid_limits_rejected(self):
        bad = [[("hip", -1, 1), ("hip", -2, 2)], [("hip", 1, 1)],
               [("hip", 2, 1)], [("bad id", -1, 1)], [("hip", False, 1)]]
        for limits in bad:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                audit_joint_limits(limits, self.samples, rig_revision="rig-a")

    def test_missing_or_extra_joint_rejected(self):
        for positions in ({"hip": 0.0}, {"hip": 0.0, "knee": 1.0, "ankle": 0.0}):
            with self.subTest(positions=positions), self.assertRaises(ValueError):
                audit_joint_limits(self.limits, [(0, "rig-a", positions)], rig_revision="rig-a")

    def test_tick_gap_and_bool_tick_rejected(self):
        cases = [[(4, "rig-a", {"hip": 0.0, "knee": 1.0}),
                  (6, "rig-a", {"hip": 0.0, "knee": 1.0})],
                 [(True, "rig-a", {"hip": 0.0, "knee": 1.0})]]
        for samples in cases:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                audit_joint_limits(self.limits, samples, rig_revision="rig-a")

    def test_revision_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            audit_joint_limits(self.limits, [(0, "rig-b", {"hip": 0.0, "knee": 1.0})],
                               rig_revision="rig-a")

    def test_bounds_and_collections_are_capped(self):
        with self.assertRaises(ValueError):
            audit_joint_limits([("j" + str(i), -1, 1) for i in range(257)],
                               self.samples, rig_revision="rig-a")
        with self.assertRaises(ValueError):
            audit_joint_limits(self.limits,
                               [(i, "rig-a", {"hip": 0.0, "knee": 1.0}) for i in range(4097)],
                               rig_revision="rig-a")

    def test_report_is_detached_from_caller_mutation(self):
        limits = [("hip", -1, 1), ("knee", 0, 2)]
        positions = {"hip": 0.2, "knee": 1.2}
        report = audit_joint_limits(limits, [(0, "rig-a", positions)], rig_revision="rig-a")
        positions["hip"] = 99
        limits[0] = ("hip", -100, 100)
        self.assertEqual(report.maximum_observed[0], ("hip", 0.2))
        self.assertEqual(len(report.digest), 64)

    def test_inexact_integer_positions_are_rejected(self):
        limits = [("hip", -(2 ** 101), 2 ** 101)]
        for value in (2 ** 53 + 1, -(2 ** 53) - 1,
                      2 ** 100 + 1, -(2 ** 100) - 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_joint_limits(limits, [(0, "rig-a", {"hip": value})],
                                   rig_revision="rig-a")

    def test_inexact_integer_bounds_are_rejected(self):
        extent = 2 ** 101
        for value in (2 ** 53 + 1, -(2 ** 53) - 1,
                      2 ** 100 + 1, -(2 ** 100) - 1):
            for side in ("lower", "upper"):
                bounds = (value, extent) if side == "lower" else (-extent, value)
                position = extent if side == "lower" else -extent
                with self.subTest(value=value, side=side), self.assertRaises(ValueError):
                    audit_joint_limits([("hip", *bounds)],
                                       [(0, "rig-a", {"hip": position})],
                                       rig_revision="rig-a")

    def test_exact_large_integers_preserve_extrema_and_distinct_digests(self):
        n = 2 ** 53
        limits = [("hip", -(2 ** 1023), 2 ** 1023)]
        values = (n - 2, n, n + 2, -n + 2, -n, -n - 2,
                  2 ** 100, -(2 ** 100), 2 ** 1023, -(2 ** 1023))
        digests = set()
        for value in values:
            with self.subTest(value=value):
                report = audit_joint_limits(limits, [(0, "rig-a", {"hip": value})],
                                            rig_revision="rig-a")
                self.assertEqual(report.minimum_observed, (("hip", value),))
                self.assertEqual(report.maximum_observed, (("hip", value),))
                self.assertIs(type(report.minimum_observed[0][1]), float)
                self.assertIs(type(report.maximum_observed[0][1]), float)
                digests.add(report.digest)
        self.assertEqual(len(digests), len(values))

    def test_exact_large_bounds_remain_distinct_in_digest(self):
        n = 2 ** 53
        for value in (n, -n):
            reports = [
                audit_joint_limits([("hip", lower, upper)],
                                   [(0, "rig-a", {"hip": value})],
                                   rig_revision="rig-a")
                for lower, upper in ((value - 2, value),
                                     (value - 2, value + 2),
                                     (value, value + 2))
            ]
            with self.subTest(value=value):
                self.assertEqual(len({report.digest for report in reports}), 3)

    def test_native_floats_and_exact_integer_float_equivalence_are_preserved(self):
        extent = float.fromhex("0x1.fffffffffffffp+1023")
        limits = [("hip", -extent, extent)]
        for value in (0.0, 0.1, -0.1, 5e-324, -5e-324, extent, -extent):
            with self.subTest(value=value):
                report = audit_joint_limits(limits, [(0, "rig-a", {"hip": value})],
                                            rig_revision="rig-a")
                self.assertEqual(report.minimum_observed, (("hip", value),))
                self.assertEqual(report.maximum_observed, (("hip", value),))
        for value in (0, 1, -1, 2 ** 53 + 2, -(2 ** 53) - 2, 2 ** 100):
            with self.subTest(value=value):
                integer_report = audit_joint_limits(
                    [("hip", -(2 ** 101), 2 ** 101)],
                    [(0, "rig-a", {"hip": value})], rig_revision="rig-a")
                float_report = audit_joint_limits(
                    [("hip", -float(2 ** 101), float(2 ** 101))],
                    [(0, "rig-a", {"hip": float(value)})], rig_revision="rig-a")
                self.assertEqual(integer_report, float_report)

if __name__ == "__main__":
    unittest.main()
