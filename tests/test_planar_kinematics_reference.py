import math
import unittest
from mojum.planar_kinematics_reference import (JointLimits, JointState, Point2,
    forward_kinematics, inverse_kinematics, solve_both_branches, within_limits)


class PlanarKinematicsReferenceTests(unittest.TestCase):
    def test_fk_known_pose(self):
        p = forward_kinematics(1.0, 1.0, JointState(0.0, 0.0))
        self.assertAlmostEqual(p.x, 2.0, places=12)
        self.assertAlmostEqual(p.y, 0.0, places=12)

    def test_ik_round_trip_both_branches(self):
        target = forward_kinematics(0.7, 0.4, JointState(0.35, 0.8))
        results = solve_both_branches(0.7, 0.4, target)
        self.assertEqual(len(results), 2)
        self.assertAlmostEqual(results[0].joints.elbow, 0.8, places=9)
        self.assertAlmostEqual(results[1].joints.elbow, -0.8, places=9)
        self.assertTrue(all(r.residual_m <= 1e-9 for r in results))

    def test_unreachable_and_limits(self):
        self.assertIsNone(inverse_kinematics(1.0, 1.0, Point2(3.0, 0.0)))
        limits = JointLimits(-0.1, 0.1, -0.1, 0.1)
        self.assertIsNone(inverse_kinematics(1.0, 1.0, Point2(1.0, 1.0), limits=limits))

    def test_validation_and_inclusive_limits(self):
        self.assertTrue(within_limits(JointState(1.0, -0.5), JointLimits(-1, 1, -0.5, 0.5)))
        with self.assertRaises(ValueError):
            JointLimits(1, -1, -1, 1)
        with self.assertRaises(ValueError):
            JointState(math.inf, 0.0)
        with self.assertRaises(ValueError):
            forward_kinematics(0.0, 1.0, JointState(0, 0))
        with self.assertRaises(ValueError):
            inverse_kinematics(1, 1, Point2(1, 0), branch="middle")

    def test_boundary_target(self):
        result = inverse_kinematics(0.5, 0.5, Point2(1.0, 0.0))
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.residual_m, 0.0, places=12)

    def test_rejects_bool_and_non_numeric_values(self):
        for value in (True, "1", None, object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    JointState(value, 0.0)
                with self.assertRaises(ValueError):
                    Point2(0.0, value)
                with self.assertRaises(ValueError):
                    JointLimits(value, 1.0, -1.0, 1.0)
                with self.assertRaises(ValueError):
                    forward_kinematics(value, 1.0, JointState(0.0, 0.0))
                with self.assertRaises(ValueError):
                    inverse_kinematics(1.0, 1.0, Point2(1.0, 0.0), tolerance_m=value)

    def test_rejects_unbounded_finite_magnitudes(self):
        with self.assertRaises(ValueError):
            Point2(1e101, 0.0)
        with self.assertRaises(ValueError):
            forward_kinematics(1e101, 1.0, JointState(0.0, 0.0))
        with self.assertRaises(ValueError):
            inverse_kinematics(1e100, 1e100, Point2(1e100, 1e100))


if __name__ == "__main__":
    unittest.main()
