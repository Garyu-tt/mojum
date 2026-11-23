import dataclasses
import tracemalloc
import unittest
from unittest.mock import patch

from mojum.motor_batch import (
    MAX_BATCH_JOINTS, SCHEMA, MotorBatchReceipt, validate_motor_batch,
)
from mojum.motor_gate import MotorGate, MotorIntent


class MotorBatchTests(unittest.TestCase):
    def setUp(self):
        self.gate = MotorGate({"hip": (-1, 1), "knee": (-2, 2), "arm": (-3, 3)}, revision=7)

    def batch(self, *commands):
        return validate_motor_batch(self.gate, list(commands), expected_revision=7)

    def test_valid_roundtrip_canonical_order_digest(self):
        a = MotorIntent("hip", 0.5, 7)
        b = MotorIntent("arm", -2, 7)
        first = self.batch(a, b)
        second = self.batch(b, a)
        self.assertEqual(first, second)
        self.assertEqual(first.schema, SCHEMA)
        self.assertEqual([c.joint for c in first.commands], ["arm", "hip"])
        self.assertEqual(len(first.sha256), 64)
        self.assertIsInstance(first, MotorBatchReceipt)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.commands = ()

    def test_negative_zero_normalization(self):
        self.assertEqual(self.batch(MotorIntent("hip", -0.0, 7)),
                         self.batch(MotorIntent("hip", 0.0, 7)))

    def test_one_bad_command_rejects_whole_batch_without_mutating_gate(self):
        intents = [MotorIntent("hip", 0.3, 7), MotorIntent("knee", 4, 7)]
        with self.assertRaises(ValueError):
            self.batch(*intents)
        self.assertEqual(self.gate.revision, 7)
        self.assertFalse(self.gate.estop_latched)
        self.assertEqual(len(intents), 2)

    def test_duplicate_joint_rejected_in_both_orders(self):
        for pair in ((MotorIntent("hip", 0, 7), MotorIntent("hip", 0.2, 7)),
                     (MotorIntent("hip", 0.2, 7), MotorIntent("hip", 0, 7))):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                self.batch(*pair)

    def test_stale_revision_in_gate_and_intent(self):
        with self.assertRaises(ValueError):
            validate_motor_batch(self.gate, [MotorIntent("hip", 0, 7)], expected_revision=8)
        with self.assertRaises(ValueError):
            self.batch(MotorIntent("hip", 0, 6))

    def test_estop_rejects_and_cannot_be_cleared_by_receipt(self):
        self.gate.trip_estop()
        with self.assertRaises(PermissionError):
            self.batch(MotorIntent("hip", 0, 7))
        self.gate.restore_revision(8)
        with self.assertRaises(PermissionError):
            validate_motor_batch(self.gate, (MotorIntent("hip", 0, 8),), expected_revision=8)

    def test_rejects_nonfinite_bool_and_unknown_joint(self):
        for joint, value in (("hip", True), ("hip", float("nan")),
                             ("hip", float("inf")), ("other", 0),
                             ("hip", 10**5000)):
            with self.subTest(joint=joint, value=type(value)), self.assertRaises(ValueError):
                self.batch(MotorIntent(joint, value, 7))

    def test_bad_batch_shape_and_revision(self):
        intent = MotorIntent("hip", 0, 7)
        for value in ([], (), [intent] * (MAX_BATCH_JOINTS+1)):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                validate_motor_batch(self.gate, value, expected_revision=7)
        for value in (iter([intent]), "hip", {"hip": intent}, None):
            with self.subTest(value=type(value)), self.assertRaises(TypeError):
                validate_motor_batch(self.gate, value, expected_revision=7)
        for revision in (True, -1, 7.0, "7"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                validate_motor_batch(self.gate, [intent], expected_revision=revision)

    def test_rejects_untrusted_intent_subclass(self):
        class Impostor(MotorIntent):
            pass
        with self.assertRaises(TypeError):
            self.batch(Impostor("hip", 0, 7))

    def test_joint_identifier_unicode_encoding_and_length(self):
        gate = MotorGate({"\ud800": (-1, 1)}, revision=7)
        with self.assertRaises(ValueError):
            validate_motor_batch(gate, [MotorIntent("\ud800", 0, 7)], expected_revision=7)
        long_name = "j" * 257
        gate = MotorGate({long_name: (-1, 1)}, revision=7)
        with self.assertRaises(ValueError):
            validate_motor_batch(gate, [MotorIntent(long_name, 0, 7)], expected_revision=7)

    def test_oversized_joint_name_fails_before_utf8_allocation(self):
        # A byte-limit checked only *after* str.encode() still allocates
        # a 2 MiB temporary object. Keep this regression about peak
        # incremental Python allocations, not elapsed runtime or hardware.
        name = "j" * (2 * 1024 * 1024)
        gate = MotorGate({name: (-1, 1)}, revision=7)
        intent = MotorIntent(name, 0, 7)
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ValueError, "identifier length"):
                validate_motor_batch(gate, [intent], expected_revision=7)
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak_bytes, 256 * 1024)

    def test_at_budget_batch_and_capacity_limit(self):
        limits = {f"j{i:03}": (-1, 1) for i in range(MAX_BATCH_JOINTS)}
        gate = MotorGate(limits, revision=11)
        intents = [MotorIntent(j, 0, 11) for j in reversed(tuple(limits))]
        receipt = validate_motor_batch(gate, intents, expected_revision=11)
        self.assertEqual(len(receipt.commands), MAX_BATCH_JOINTS)

    def test_revalidated_gate_after_revision_changes_mid_validation(self):
        original = MotorGate.validate

        def revision_change(gate, intent):
            target = original(gate, intent)
            gate.restore_revision(gate.revision + 1)
            return target

        with patch.object(MotorGate, "validate", revision_change):
            with self.assertRaisesRegex(ValueError, "revision changed"):
                self.batch(MotorIntent("hip", 0, 7))
        self.assertEqual(self.gate.revision, 8)

    def test_revalidated_estop_when_triggered_mid_validation(self):
        original = MotorGate.validate

        def stop_during_validation(gate, intent):
            target = original(gate, intent)
            gate.trip_estop()
            return target

        with patch.object(MotorGate, "validate", stop_during_validation):
            with self.assertRaises(PermissionError):
                self.batch(MotorIntent("hip", 0, 7))
        self.assertTrue(self.gate.estop_latched)

    def test_hash_binds_revision_target_and_joint(self):
        original = self.batch(MotorIntent("hip", 0.1, 7))
        self.assertNotEqual(original.sha256, self.batch(MotorIntent("hip", 0.2, 7)).sha256)
        self.assertNotEqual(original.sha256, self.batch(MotorIntent("arm", 0.1, 7)).sha256)
        other = MotorGate({"hip": (-1, 1)}, revision=8)
        changed = validate_motor_batch(other, [MotorIntent("hip", 0.1, 8)], expected_revision=8)
        self.assertNotEqual(original.sha256, changed.sha256)


if __name__ == "__main__":
    unittest.main()
