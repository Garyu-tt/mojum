import unittest
from mojum.motor_gate import MotorGate, MotorIntent


class MotorGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = MotorGate({"hip": (-1.0, 1.0)}, revision=3)

    def test_valid_bounded_intent(self):
        self.assertEqual(self.gate.validate(MotorIntent("hip", 1, 3)), 1.0)

    def test_stale_revision_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("hip", 0, 2))

    def test_unknown_joint_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("elbow", 0, 3))

    def test_nonfinite_and_bool_rejected(self):
        for v in (float("nan"), float("inf"), -float("inf"), True):
            with self.subTest(v=v), self.assertRaises(ValueError):
                self.gate.validate(MotorIntent("hip", v, 3))

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("hip", 1.01, 3))

    def test_estop_survives_restore(self):
        self.gate.trip_estop()
        self.gate.restore_revision(4)
        with self.assertRaises(PermissionError):
            self.gate.validate(MotorIntent("hip", 0, 4))

    def test_revision_rollback_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.restore_revision(2)

    def test_invalid_limits(self):
        for limits in ({"hip": (1, 1)}, {"hip": (float("nan"), 1)}, {}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                MotorGate(limits)

    def test_untrusted_joint_type_rejected(self):
        for value in ([], {}, None, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.gate.validate(MotorIntent(value, 0, 3))

    def test_untrusted_limits_mapping_rejected(self):
        for value in (["hip"], "hip", 14, True, None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MotorGate(value)

    def test_huge_int_not_accepted_as_finite_target(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("hip", 10**5000, 3))

    def test_huge_int_not_accepted_as_finite_limit(self):
        with self.assertRaises(ValueError):
            MotorGate({"hip": (-1.0, 10**5000)})

    def test_limits_are_deep_copy_and_read_only(self):
        source = {"hip": [-1.0, 1.0]}
        gate = MotorGate(source, revision=3)
        source["hip"][1] = 999
        self.assertEqual(gate.limits["hip"], (-1.0, 1.0))
        with self.assertRaises(TypeError):
            gate.limits["hip"] = (-999.0, 999.0)
        with self.assertRaises(ValueError):
            gate.validate(MotorIntent("hip", 20, 3))

    def test_public_estop_latch_cannot_be_reassigned(self):
        self.gate.trip_estop()
        with self.assertRaises(AttributeError):
            self.gate.estop_latched = False
        self.assertTrue(self.gate.estop_latched)
        with self.assertRaises(PermissionError):
            self.gate.validate(MotorIntent("hip", 0, 3))

    def test_public_revision_cannot_be_overwritten(self):
        with self.assertRaises(AttributeError):
            self.gate.revision = 444
        self.assertEqual(self.gate.revision, 3)
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("hip", 0, 444))

    def test_public_limits_cannot_be_replaced(self):
        with self.assertRaises(AttributeError):
            self.gate.limits = {"hip": (-10000, 10000)}

    def test_invalid_intent_revision_bool(self):
        with self.assertRaises(ValueError):
            self.gate.validate(MotorIntent("hip", 0, True))

    def test_limits_dont_accept_empty_joint_name(self):
        for name in ("", "  "):
            with self.assertRaises(ValueError):
                MotorGate({name: (-1, 1)})

    def test_estop_is_idempotent(self):
        self.gate.trip_estop()
        self.gate.trip_estop()
        self.assertTrue(self.gate.estop_latched)

    def test_no_physical_actuation_interface(self):
        for op in ("actuate", "set_torque", "hardware_reset", "clear_estop"):
            self.assertFalse(hasattr(self.gate, op))


if __name__ == "__main__":
    unittest.main()

