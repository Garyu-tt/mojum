"""Numerically stable measured-pose endpoints without relaxing frame guards."""
from fractions import Fraction
import math
import random
import unittest

from mojum.vrm_pose_interpolation import (
    PoseInterpolationError, interpolate_measured_poses, verify_interpolated_pose,
)


def frame(identity, timestamp, coordinates):
    return dict(schema="mojum.vrm-measured-pose/1", classification="PUBLIC_SYNTHETIC",
                frame_id=identity, body_epoch="body", rig_revision=1,
                coordinate_system="METER_RIGHT_HANDED_Y_UP", timestamp_ns=timestamp,
                joints={"root": coordinates})


def interpolate(left, right, target, duration=2**63 - 1, offset=0):
    return interpolate_measured_poses(
        frame("a", offset, (left, 0.0, 0.0)),
        frame("b", offset + duration, (right, 0.0, 0.0)),
        target_timestamp_ns=offset + target, max_joint_delta=100)


class EndpointPrecisionTests(unittest.TestCase):
    def test_source_endpoint_coordinates_preserve_exact_floats_and_signed_zero(self):
        left, right = (1.0, -0.0, 1e-300), (1e-20, 0.0, -0.0)
        a, b = frame("a", 100, left), frame("b", 200, right)
        for target, expected in ((100, left), (200, right)):
            with self.subTest(target=target):
                result = interpolate_measured_poses(a, b, target_timestamp_ns=target)
                self.assertEqual(tuple(value.hex() for value in result.coordinates[0]),
                                 tuple(value.hex() for value in expected))
                self.assertTrue(verify_interpolated_pose(result))

    def test_penultimate_nanosecond_is_not_treated_as_endpoint(self):
        duration = 2**63 - 1
        for right in (0.0, 1e-20, -1e-20):
            with self.subTest(right=right):
                result = interpolate(1.0, right, duration - 1)
                expected = float((Fraction(1.0) + Fraction(right) * (duration - 1)) / duration)
                self.assertEqual(result.ratio, 1.0)  # Approximate metadata only.
                self.assertNotEqual(result.coordinates[0][0], right)
                self.assertAlmostEqual(result.coordinates[0][0], expected, delta=math.ulp(expected))
                self.assertTrue(verify_interpolated_pose(result))

    def test_near_first_and_second_frames_are_symmetric(self):
        duration = 2**63 - 1
        for distance in (1, 3, 17, 1024, 10**9):
            forward = interpolate(1e-20, 1.0, distance)
            reverse = interpolate(1.0, 1e-20, duration - distance)
            self.assertEqual(forward.coordinates, reverse.coordinates)

    def test_timestamp_translation_preserves_coordinates(self):
        duration = 10**12
        for target in (0, 1, duration // 2, duration - 1, duration):
            low = interpolate(1.0, 1e-20, target, duration)
            high = interpolate(1.0, 1e-20, target, duration, 2**63 - 1 - duration)
            self.assertEqual(low.coordinates, high.coordinates)
            self.assertEqual(low.ratio, high.ratio)

    def test_endpoints_still_validate_opposite_frame_and_discontinuity(self):
        for target in (0, 100):
            with self.subTest(target=target), self.assertRaises(PoseInterpolationError):
                interpolate_measured_poses(frame("a", 0, (0.0, 0.0, 0.0)),
                                           frame("b", 100, (3.0, 0.0, 0.0)),
                                           target_timestamp_ns=target)
        a, b = frame("a", 0, (0.0, 0.0, 0.0)), frame("b", 100, (1.0, 0.0, 0.0))
        b["body_epoch"] = "other"
        with self.assertRaises(PoseInterpolationError):
            interpolate_measured_poses(a, b, target_timestamp_ns=0)

    def test_random_interiors_match_exact_rational_oracle_with_roundoff_bound(self):
        rng = random.Random(1500)
        for index in range(1000):
            duration = rng.randrange(2, 2**63)
            target = rng.choice((1, duration - 1, rng.randrange(1, duration)))
            left = rng.uniform(-900, 900)
            right = left + rng.uniform(-99, 99)
            result = interpolate(left, right, target, duration)
            expected = float((Fraction(left) * (duration - target)
                              + Fraction(right) * target) / duration)
            bound = 3 * math.ulp(max(abs(left), abs(right)))
            with self.subTest(index=index):
                actual = result.coordinates[0][0]
                self.assertAlmostEqual(actual, expected, delta=bound)
                self.assertTrue(min(left, right) - bound <= actual <= max(left, right) + bound)
                self.assertTrue(verify_interpolated_pose(result))

    def test_endpoint_result_stays_detached_from_input_mutation(self):
        a, b = frame("a", 0, (1.0, 0.0, 0.0)), frame("b", 10, (1e-20, -0.0, 0.0))
        result = interpolate_measured_poses(a, b, target_timestamp_ns=10)
        b["joints"]["root"] = (99.0, 99.0, 99.0)
        self.assertEqual(result.coordinates[0], (1e-20, -0.0, 0.0))
        self.assertTrue(verify_interpolated_pose(result))


if __name__ == "__main__":
    unittest.main()
