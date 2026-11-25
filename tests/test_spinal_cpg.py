import dataclasses
import unittest

from mojum.spinal_cpg import (
    BilateralCPG,
    CPGError,
    CPGProfile,
    FRAME_SCHEMA,
    HALF_CYCLE,
    MAX_TICKS,
    PHASE_MICROTURNS,
    SensoryFeedback,
)


def profile(**changes):
    values = {
        "revision": 7,
        "tick_us": 10_000,
        "minimum_frequency_millihz": 500,
        "base_frequency_millihz": 1_000,
        "maximum_frequency_millihz": 1_500,
        "amplitude_milli": 800,
        "coupling_gain_milli": 500,
        "sensory_phase_gain": 10_000,
        "maximum_phase_correction": 25_000,
    }
    values.update(changes)
    return CPGProfile(**values)


def feedback(tick, **changes):
    values = {"tick": tick, "profile_revision": 7}
    values.update(changes)
    return SensoryFeedback(**values)


def phase_error(snapshot):
    error = (
        snapshot.right_phase_microturns
        - snapshot.left_phase_microturns
        - HALF_CYCLE
    ) % PHASE_MICROTURNS
    return error - PHASE_MICROTURNS if error >= HALF_CYCLE else error


class ProfileAdmissionTests(unittest.TestCase):
    def test_profile_is_immutable_and_digest_is_deterministic(self):
        first = profile()
        second = profile()
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(first.sha256), 64)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.amplitude_milli = 1

    def test_profile_rejects_invalid_native_types_and_bounds(self):
        invalid = (
            {"revision": True},
            {"revision": -1},
            {"tick_us": 999},
            {"tick_us": 100_001},
            {"minimum_frequency_millihz": 0},
            {"maximum_frequency_millihz": 5_001},
            {"amplitude_milli": 0},
            {"amplitude_milli": 1_001},
            {"coupling_gain_milli": 0},
            {"coupling_gain_milli": True},
            {"sensory_phase_gain": -1},
            {"maximum_phase_correction": 100_001},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(CPGError):
                profile(**change)

    def test_profile_rejects_inverted_or_uncontained_frequency_and_gain(self):
        for change in (
            {"minimum_frequency_millihz": 1_100},
            {"maximum_frequency_millihz": 900},
            {"sensory_phase_gain": 30_000, "maximum_phase_correction": 25_000},
            {
                "tick_us": 100_000,
                "maximum_frequency_millihz": 5_000,
                "base_frequency_millihz": 5_000,
            },
        ):
            with self.subTest(change=change), self.assertRaises(CPGError):
                profile(**change)

    def test_profile_accepts_exact_safe_combined_advance_boundary(self):
        boundary = profile(
            tick_us=100_000,
            base_frequency_millihz=4_750,
            maximum_frequency_millihz=4_750,
            sensory_phase_gain=25_000,
        )
        frame = BilateralCPG(boundary).step(
            SensoryFeedback(1, boundary.revision, right_load_milli=1_000)
        )
        self.assertEqual(frame.left_phase_microturns, HALF_CYCLE)
        self.assertEqual(frame.right_phase_microturns, 950_000)
        self.assertEqual(frame.phase_correction_microturns, 25_000)


class FeedbackAdmissionTests(unittest.TestCase):
    def test_feedback_requires_explicit_synthetic_bounded_native_fields(self):
        invalid = (
            {"tick": 0},
            {"tick": True},
            {"profile_revision": -1},
            {"frequency_delta_millihz": 5_001},
            {"frequency_delta_millihz": True},
            {"left_load_milli": -1},
            {"right_load_milli": 1_001},
            {"synthetic": False},
            {"synthetic": 1},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(CPGError):
                values = {"tick": 1, "profile_revision": 7}
                values.update(change)
                SensoryFeedback(**values)

    def test_step_rejects_wrong_type_tick_gap_replay_and_revision(self):
        controller = BilateralCPG(profile())
        initial = controller.snapshot()
        for value in (None, {}, True):
            with self.subTest(value=value), self.assertRaises(CPGError):
                controller.step(value)
            self.assertEqual(controller.snapshot(), initial)
        with self.assertRaisesRegex(CPGError, "contiguous"):
            controller.step(feedback(2))
        self.assertEqual(controller.snapshot(), initial)
        with self.assertRaisesRegex(CPGError, "revision"):
            controller.step(SensoryFeedback(1, 8))
        self.assertEqual(controller.snapshot(), initial)
        controller.step(feedback(1))
        admitted = controller.snapshot()
        with self.assertRaisesRegex(CPGError, "contiguous"):
            controller.step(feedback(1))
        self.assertEqual(controller.snapshot(), admitted)


class OscillatorTests(unittest.TestCase):
    def test_neutral_step_is_bilateral_bounded_and_has_no_actuation(self):
        controller = BilateralCPG(profile())
        frame = controller.step(feedback(1))
        self.assertEqual(frame.schema, FRAME_SCHEMA)
        self.assertEqual(frame.tick, 1)
        self.assertEqual(frame.frequency_millihz, 1_000)
        self.assertEqual(frame.left_phase_microturns, 10_000)
        self.assertEqual(frame.right_phase_microturns, 510_000)
        self.assertEqual(frame.left_output_milli, 32)
        self.assertEqual(frame.right_output_milli, -32)
        self.assertLessEqual(abs(frame.left_output_milli), 800)
        self.assertLessEqual(abs(frame.right_output_milli), 800)
        self.assertEqual(frame.phase_correction_microturns, 0)
        self.assertEqual(frame.waveform, "FIXED_POINT_TRIANGLE")
        self.assertFalse(frame.physical_actuation)
        self.assertFalse(hasattr(controller, "actuate"))

    def test_frequency_modulation_clamps_to_profile(self):
        controller = BilateralCPG(profile())
        fast = controller.step(feedback(1, frequency_delta_millihz=5_000))
        slow = controller.step(feedback(2, frequency_delta_millihz=-5_000))
        self.assertEqual(fast.frequency_millihz, 1_500)
        self.assertEqual(slow.frequency_millihz, 500)
        self.assertEqual(fast.left_phase_microturns, 15_000)
        self.assertEqual(slow.left_phase_microturns, 20_000)

    def test_asymmetric_load_perturbs_phase_and_neutral_coupling_recovers(self):
        controller = BilateralCPG(profile())
        perturbed = controller.step(feedback(1, right_load_milli=1_000))
        self.assertEqual(perturbed.phase_correction_microturns, 5_000)
        displaced = controller.snapshot()
        self.assertEqual(phase_error(displaced), -10_000)

        recovered = controller.step(feedback(2))
        self.assertEqual(recovered.coupling_error_before_microturns, -10_000)
        self.assertEqual(recovered.phase_correction_microturns, -5_000)
        self.assertEqual(phase_error(controller.snapshot()), 0)

    def test_correction_never_reverses_or_exceeds_half_base_advance(self):
        slow = profile(
            minimum_frequency_millihz=10,
            base_frequency_millihz=10,
            maximum_frequency_millihz=10,
            sensory_phase_gain=25_000,
        )
        controller = BilateralCPG(slow)
        frame = controller.step(
            SensoryFeedback(1, slow.revision, right_load_milli=1_000)
        )
        advance = 100
        self.assertEqual(frame.phase_correction_microturns, advance // 2)
        self.assertEqual(frame.left_phase_microturns, advance + advance // 2)
        self.assertEqual(
            frame.right_phase_microturns, HALF_CYCLE + advance - advance // 2
        )

    def test_long_run_keeps_phases_and_outputs_bounded(self):
        controller = BilateralCPG(profile())
        for tick in range(1, 20_001):
            frame = controller.step(
                feedback(
                    tick,
                    frequency_delta_millihz=(tick % 41) - 20,
                    left_load_milli=tick % 1_001,
                    right_load_milli=(tick * 7) % 1_001,
                )
            )
            self.assertTrue(0 <= frame.left_phase_microturns < PHASE_MICROTURNS)
            self.assertTrue(0 <= frame.right_phase_microturns < PHASE_MICROTURNS)
            self.assertLessEqual(abs(frame.left_output_milli), 800)
            self.assertLessEqual(abs(frame.right_output_milli), 800)
        self.assertEqual(controller.tick, 20_000)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_bound_to_full_profile(self):
        controller = BilateralCPG(profile())
        controller.step(feedback(1))
        snapshot = controller.snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.tick = 8
        with self.assertRaisesRegex(CPGError, "different profile"):
            BilateralCPG.from_snapshot(profile(amplitude_milli=799), snapshot)

    def test_snapshot_detects_tampering_schema_and_wrong_types(self):
        controller = BilateralCPG(profile())
        snapshot = controller.snapshot()
        changes = (
            {"schema": "mojum.spinal-cpg.snapshot/999"},
            {"tick": snapshot.tick + 1},
            {"left_phase_microturns": PHASE_MICROTURNS},
            {"frequency_remainder": 1_000},
            {"state_sha256": "0" * 64},
            {"profile_sha256": "G" * 64},
            {"profile_revision": True},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(CPGError):
                BilateralCPG.from_snapshot(profile(), dataclasses.replace(snapshot, **change))
        with self.assertRaises(CPGError):
            BilateralCPG.from_snapshot(profile(), dataclasses.asdict(snapshot))

    def test_restore_replays_identical_continuation(self):
        first = BilateralCPG(profile())
        for tick in range(1, 51):
            first.step(
                feedback(
                    tick,
                    frequency_delta_millihz=(tick % 9) - 4,
                    left_load_milli=(tick * 3) % 101,
                    right_load_milli=(tick * 5) % 101,
                )
            )
        restored = BilateralCPG.from_snapshot(profile(), first.snapshot())
        for tick in range(51, 201):
            item = feedback(
                tick,
                frequency_delta_millihz=(tick % 9) - 4,
                left_load_milli=(tick * 3) % 101,
                right_load_milli=(tick * 5) % 101,
            )
            self.assertEqual(first.step(item), restored.step(item))
        self.assertEqual(first.snapshot(), restored.snapshot())

    def test_valid_max_tick_snapshot_restores_but_cannot_step(self):
        controller = BilateralCPG(profile())
        controller._tick = MAX_TICKS
        restored = BilateralCPG.from_snapshot(profile(), controller.snapshot())
        with self.assertRaisesRegex(CPGError, "budget exhausted"):
            restored.step(feedback(MAX_TICKS))


if __name__ == "__main__":
    unittest.main()
