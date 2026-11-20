import unittest
from motor_gate import MotorGate, MotorIntent


class MotorGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = MotorGate({'hip': (-1.0, 1.0)}, revision=3)

    def test_valid_bounded_intent(self):
        self.assertEqual(self.gate.validate(MotorIntent('hip', 1, 3)), 1.0)

    def test_stale_revision_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent('hip', 0, 2))

    def test_unknown_joint_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent('elbow', 0, 3))

    def test_nonfinite_and_bool_rejected(self):
        for v in (float('nan'), float('inf'), True):
            with self.subTest(v=v), self.assertRaises(ValueError):
                self.gate.validate(MotorIntent('hip', v, 3))

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent('hip', 1.01, 3))

    def test_estop_survives_restore(self):
        self.gate.trip_estop()
        self.gate.restore_revision(4)
        with self.assertRaises(PermissionError):
            self.gate.validate(MotorIntent('hip', 0, 4))

    def test_revision_rollback_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.restore_revision(2)

    def test_invalid_limits(self):
        for limits in ({'hip': (1, 1)}, {'hip': (float('nan'), 1)}, {}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                MotorGate(limits)


if __name__ == '__main__':
    unittest.main()
