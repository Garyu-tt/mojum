import unittest
from dataclasses import replace

from mojum.wardrobe_state import (
    WardrobeError, initial_state, transition, _verify_state,
)


class WardrobeStateTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state("rig-r1")

    def apply(self, state, action, slot, garment, op):
        return transition(state, action=action, slot=slot, garment_id=garment,
                          operation_id=op, expected_revision=state.revision,
                          body_revision="rig-r1")

    def test_equip_and_deterministic_replay(self):
        a, receipt_a = self.apply(self.state, "equip", "torso", "shirt-1", "op-1")
        b, receipt_b = self.apply(self.state, "equip", "torso", "shirt-1", "op-1")
        self.assertEqual(a, b)
        self.assertEqual(receipt_a, receipt_b)
        self.assertEqual(a.equipped, (("torso", "shirt-1"),))
        self.assertEqual(a.sequence, 1)

    def test_unequip_and_drop(self):
        equipped, _ = self.apply(self.state, "equip", "head", "hat-1", "op-1")
        removed, receipt = self.apply(equipped, "unequip", "head", "hat-1", "op-2")
        self.assertEqual(removed.equipped, ())
        self.assertEqual(receipt.garment_id, "hat-1")

    def test_replace_is_atomic(self):
        equipped, _ = self.apply(self.state, "equip", "torso", "shirt-1", "op-1")
        changed, receipt = self.apply(equipped, "replace", "torso", "shirt-2", "op-2")
        self.assertEqual(changed.equipped, (("torso", "shirt-2"),))
        self.assertEqual(receipt.previous_revision, equipped.revision)

    def test_stale_revision_rejected_without_mutation(self):
        with self.assertRaisesRegex(WardrobeError, "stale"):
            transition(self.state, action="equip", slot="torso", garment_id="s1",
                       operation_id="op-1", expected_revision="bad",
                       body_revision="rig-r1")
        self.assertEqual(self.state, initial_state("rig-r1"))

    def test_body_revision_mismatch(self):
        with self.assertRaisesRegex(WardrobeError, "body revision"):
            transition(self.state, action="equip", slot="torso", garment_id="s1",
                       operation_id="op-1", expected_revision=self.state.revision,
                       body_revision="rig-r2")

    def test_duplicate_operation_rejected(self):
        equipped, _ = self.apply(self.state, "equip", "torso", "s1", "op-1")
        with self.assertRaisesRegex(WardrobeError, "duplicate"):
            self.apply(equipped, "unequip", "torso", "s1", "op-1")

    def test_duplicate_garment_and_slot_rejected(self):
        equipped, _ = self.apply(self.state, "equip", "torso", "s1", "op-1")
        with self.assertRaises(WardrobeError):
            self.apply(equipped, "equip", "head", "s1", "op-2")
        with self.assertRaises(WardrobeError):
            self.apply(equipped, "equip", "torso", "s2", "op-3")

    def test_invalid_transitions_do_not_advance(self):
        for action, slot, garment in [
            ("unequip", "head", "hat"),
            ("drop", "head", "hat"),
            ("replace", "head", "hat"),
            ("equip", "head", None),
            ("teleport", "head", "hat"),
        ]:
            with self.subTest(action=action):
                with self.assertRaises(WardrobeError):
                    self.apply(self.state, action, slot, garment, "op-x")
        self.assertEqual(self.state.sequence, 0)

    def test_bool_and_subclass_like_bad_values_rejected(self):
        with self.assertRaises(WardrobeError):
            transition(self.state, action="equip", slot="torso", garment_id=True,
                       operation_id="op-1", expected_revision=self.state.revision,
                       body_revision="rig-r1")

    def test_tampered_state_rejected(self):
        tampered = replace(self.state, sequence=1)
        with self.assertRaisesRegex(WardrobeError, "revision"):
            _verify_state(tampered)

    def test_bounded_history(self):
        state = self.state
        for i in range(35):
            state, _ = self.apply(state, "equip", "torso", f"g{i}", f"op-{i}")
            state, _ = self.apply(state, "unequip", "torso", f"g{i}", f"op-{i}-off")
        self.assertLessEqual(len(state.recent_operations), 32)

    def test_unsupported_slot(self):
        with self.assertRaises(WardrobeError):
            self.apply(self.state, "equip", "skin", "shirt", "op-1")


if __name__ == "__main__":
    unittest.main()
