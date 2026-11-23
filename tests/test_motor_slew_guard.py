"""Adversarial stdlib tests for virtual, never physical, motor slew admission."""

import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

from mojum.motor_slew_guard import (
    MAX_JOINTS, MAX_TICK, SCHEMA, MotorSlewError, MotorSlewGuard,
)


def guard():
    return MotorSlewGuard(
        {"elbow": (-1.0, 1.0, 0.125), "wrist": (0.0, 2.0, 0.25)},
        {"elbow": 0.0, "wrist": 1.0},
    )


class MotorSlewGuardTests(unittest.TestCase):
    def test_accepted_adjacent_frames_and_exact_limit(self):
        gate = guard()
        r = gate.advance({"elbow": 0.125, "wrist": 0.75}, tick=1, expected_revision=0)
        self.assertEqual((r.schema, r.tick, r.revision), (SCHEMA, 1, 1))
        self.assertEqual(dict(r.targets), {"elbow": 0.125, "wrist": 0.75})
        r2 = gate.advance({"elbow": 0.0, "wrist": 1.0}, tick=2, expected_revision=1)
        self.assertEqual((r2.tick, r2.revision), (2, 2))

    def test_atomic_reject_if_only_one_joint_too_fast(self):
        gate = guard()
        original = gate.receipt()
        with self.assertRaisesRegex(MotorSlewError, "slew"):
            gate.advance({"elbow": 0.2, "wrist": 1.25}, tick=1, expected_revision=0)
        self.assertEqual(gate.receipt(), original)
        self.assertEqual(gate.advance({"elbow": 0.125, "wrist": 1.25}, tick=1, expected_revision=0).tick, 1)

    def test_no_skipped_tick_even_if_motion_small(self):
        gate = guard()
        with self.assertRaisesRegex(MotorSlewError, "non-adjacent"):
            gate.advance({"elbow": 0.0, "wrist": 1.0}, tick=10, expected_revision=0)
        self.assertEqual(gate.tick, 0)

    def test_revision_is_cas_and_old_receipt_stays_immutable(self):
        gate = guard()
        before = gate.receipt()
        gate.advance({"elbow": 0.1, "wrist": 1.0}, tick=1, expected_revision=0)
        with self.assertRaises(MotorSlewError):
            gate.advance({"elbow": 0.2, "wrist": 1.0}, tick=2, expected_revision=0)
        self.assertEqual(before.targets, (("elbow", 0.0), ("wrist", 1.0)))
        with self.assertRaises(FrozenInstanceError):
            before.revision = 100
        self.assertEqual(gate.revision, 1)

    def test_e_stop_is_permanent_and_does_not_advance(self):
        gate = guard()
        original = gate.receipt()
        gate.trip_estop()
        gate.trip_estop()
        self.assertTrue(gate.estop_latched)
        with self.assertRaisesRegex(MotorSlewError, "e-stop"):
            gate.advance({"elbow": 0.0, "wrist": 1.0}, tick=1, expected_revision=0)
        self.assertEqual(gate.receipt(), original)

    def test_extra_and_missing_joint_reject(self):
        gate = guard()
        for targets in ({"elbow": 0.1}, {"elbow": 0.1, "wrist": 1.0, "hidden": 0.0}):
            with self.subTest(targets=targets), self.assertRaises(MotorSlewError):
                gate.advance(targets, tick=1, expected_revision=0)
        self.assertEqual(gate.tick, 0)

    def test_bools_nan_infinity_and_untrusted_numbers(self):
        gate = guard()
        for bad in (True, False, float("nan"), float("inf"), -float("inf"), "0.1", [], 10**500):
            with self.subTest(bad=repr(bad)), self.assertRaises(MotorSlewError):
                gate.advance({"elbow": bad, "wrist": 1.0}, tick=1, expected_revision=0)
        self.assertEqual(gate.receipt().tick, 0)

    def test_invalid_ticks_and_revisions(self):
        gate = guard()
        for bad in (True, -1, 1.5, "1", 10**100):
            with self.subTest(bad=bad):
                with self.assertRaises(MotorSlewError):
                    gate.advance({"elbow": 0.1, "wrist": 1.0}, tick=bad, expected_revision=0)
                with self.assertRaises(MotorSlewError):
                    gate.advance({"elbow": 0.1, "wrist": 1.0}, tick=1, expected_revision=bad)
        self.assertEqual(gate.tick, 0)

    def test_tick_revision_overflow_is_rejected_before_mutation(self):
        for tick, revision in ((MAX_TICK, 0), (0, MAX_TICK)):
            gate = MotorSlewGuard({"j": (0, 1, 0.1)}, {"j": 0}, tick=tick, revision=revision)
            before = gate.receipt()
            with self.assertRaises(MotorSlewError):
                gate.advance({"j": 0.05}, tick=min(MAX_TICK, tick + 1), expected_revision=revision)
            self.assertEqual(gate.receipt(), before)

    def test_initial_and_limits_are_copied(self):
        limits = {"elbow": (-1, 1, 0.125)}
        initial = {"elbow": 0.0}
        gate = MotorSlewGuard(limits, initial)
        limits["elbow"] = (-1, 1, 1)
        initial["elbow"] = 0.9
        with self.assertRaises(MotorSlewError):
            gate.advance({"elbow": 0.2}, tick=1, expected_revision=0)
        self.assertEqual(dict(gate.receipt().targets)["elbow"], 0.0)

    def test_accepted_target_dict_is_copied(self):
        gate = guard()
        frame = {"elbow": 0.1, "wrist": 1.0}
        receipt = gate.advance(frame, tick=1, expected_revision=0)
        frame["elbow"] = 0.9
        self.assertEqual(dict(receipt.targets)["elbow"], 0.1)
        self.assertEqual(dict(gate.receipt().targets)["elbow"], 0.1)

    def test_reject_lossy_large_int_before_bounds_or_slew(self):
        # Without a lossless check, hi+1 rounds to hi as IEEE binary64
        # and an out-of-bounds caller target is silently accepted.
        hi = 2**54
        gate = MotorSlewGuard({"j": (0, hi, 10)}, {"j": hi - 10})
        before = gate.receipt()
        with self.assertRaisesRegex(MotorSlewError, "precision"):
            gate.advance({"j": hi + 1}, tick=1, expected_revision=0)
        self.assertEqual(gate.receipt(), before)
        with self.assertRaisesRegex(MotorSlewError, "precision"):
            MotorSlewGuard({"j": (0, hi + 1, 10)}, {"j": 0})
        with self.assertRaisesRegex(MotorSlewError, "precision"):
            MotorSlewGuard({"j": (0, hi, 10)}, {"j": hi - 9})
        # Exactly representable large integers remain supported.
        self.assertEqual(
            gate.advance({"j": hi}, tick=1, expected_revision=0).targets,
            (("j", float(hi)),),
        )

    def test_invalid_joint_definition(self):
        bad_limits = [
            {}, {"": (0, 1, 0.1)}, {"j k": (0, 1, 0.1)},
            {"j": (0, 1)}, {"j": [0, 1, 0.1]},
            {"j": (0, 0, 0.1)}, {"j": (0, 1, 0)},
            {"j": (0, 1, -1)}, {"j": (True, 1, 0.1)},
            {"j": (0, float("inf"), 0.1)},
            {"j": (-1e308, 1e308, 1.0)},
            {"j": (0, 1, float("nan"))},
        ]
        for item in bad_limits:
            with self.subTest(item=repr(item)), self.assertRaises(MotorSlewError):
                MotorSlewGuard(item, {"j": 0})

    def test_count_boundary_and_shape(self):
        many = {f"j{i}": (0, 1, 1) for i in range(MAX_JOINTS)}
        gate = MotorSlewGuard(many, {name: 0 for name in many})
        self.assertEqual(len(gate.receipt().targets), MAX_JOINTS)
        many["overflow"] = (0, 1, 1)
        with self.assertRaises(MotorSlewError):
            MotorSlewGuard(many, {name: 0 for name in many})

    def test_initial_mismatch_and_out_of_bounds(self):
        for initial in ({"j": 0, "x": 0}, {}, {"j": 2}, {"j": True}):
            with self.subTest(initial=initial), self.assertRaises(MotorSlewError):
                MotorSlewGuard({"j": (0, 1, 0.1)}, initial)

    def test_reject_mapping_subclasses_with_custom_accessors(self):
        class Hostile(dict):
            def keys(self):
                raise AssertionError("untrusted code executed")
        gate = guard()
        with self.assertRaises(MotorSlewError):
            gate.advance(Hostile(), tick=1, expected_revision=0)
        with self.assertRaises(MotorSlewError):
            MotorSlewGuard(Hostile(), {})

    def test_utf8_controls_and_unknown_name_are_rejected(self):
        for name in ("\ud800", "актер", "foo\x00bar", "A" * 65, ".hidden", 7):
            with self.subTest(name=repr(name)), self.assertRaises(MotorSlewError):
                MotorSlewGuard({name: (0, 1, 0.1)}, {name: 0})

    def test_boundary_target_with_small_slew(self):
        gate = MotorSlewGuard({"j": (0, 1, 0.5)}, {"j": 0.5})
        self.assertEqual(gate.advance({"j": 1.0}, tick=1, expected_revision=0).targets, (("j", 1.0),))
        self.assertEqual(gate.advance({"j": 0.5}, tick=2, expected_revision=1).targets, (("j", 0.5),))

    def test_failed_target_never_changes_revision_or_tick(self):
        gate = guard()
        for _ in range(3):
            with self.assertRaises(MotorSlewError):
                gate.advance({"elbow": 1, "wrist": 1}, tick=1, expected_revision=0)
        self.assertEqual(gate.receipt(), guard().receipt())

    def test_reject_non_dict_frame(self):
        gate = guard()
        for item in ([], (), None, "", (("elbow", 0.1), ("wrist", 1.0))):
            with self.subTest(item=item), self.assertRaises(MotorSlewError):
                gate.advance(item, tick=1, expected_revision=0)

    def test_concurrent_same_revision_admits_exactly_one_frame(self):
        class SlowAdmissionGuard(MotorSlewGuard):
            @staticmethod
            def _normalize_targets(targets, limits):
                # Widen validation so the old unlocked check/commit race is deterministic.
                time.sleep(0.03)
                return MotorSlewGuard._normalize_targets(targets, limits)

        gate = SlowAdmissionGuard({"j": (0.0, 1.0, 0.5)}, {"j": 0.0})

        def submit(value):
            try:
                gate.advance({"j": value}, tick=1, expected_revision=0)
                return "accepted"
            except MotorSlewError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (0.1, 0.2)))
        self.assertEqual(sorted(results), ["accepted", "rejected"])
        self.assertEqual((gate.tick, gate.revision), (1, 1))

    def test_estop_remains_latched_across_rejected_frame(self):
        gate = guard()
        gate.trip_estop()
        with self.assertRaisesRegex(MotorSlewError, "e-stop"):
            gate.advance({"elbow": 0.0, "wrist": 1.0}, tick=1, expected_revision=0)
        self.assertTrue(gate.estop_latched)
        self.assertEqual((gate.tick, gate.revision), (0, 0))

    def test_empty_joint_set_fails_closed(self):
        with self.assertRaises(MotorSlewError):
            MotorSlewGuard({}, {})


if __name__ == "__main__":
    unittest.main()
