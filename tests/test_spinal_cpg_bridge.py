import unittest
from dataclasses import replace

from mojum.motor_gate import MotorGate
from mojum.spinal_cpg import BilateralCPG, CPGProfile, SensoryFeedback
from mojum.spinal_cpg_bridge import (
    CPGBridgeError,
    JointChannel,
    feedback_from_loads,
    intents_from_frame,
)


def make_frame():
    profile = CPGProfile(
        revision=3,
        tick_us=10_000,
        minimum_frequency_millihz=500,
        base_frequency_millihz=1_000,
        maximum_frequency_millihz=1_500,
        amplitude_milli=1_000,
        coupling_gain_milli=100,
        sensory_phase_gain=50,
        maximum_phase_correction=100,
    )
    cpg = BilateralCPG(profile)
    frame = cpg.step(SensoryFeedback(tick=1, profile_revision=3))
    return frame, profile, cpg.snapshot()


class CPGBridgeTests(unittest.TestCase):
    def setUp(self):
        self.gate = MotorGate(
            {"left_hip": (-1.0, 1.0), "right_hip": (-1.0, 1.0)},
            revision=3,
        )
        self.frame, self.profile, self.snapshot = make_frame()
        self.channels = (
            JointChannel("left_hip", "left", 0, 500),
            JointChannel("right_hip", "right", 0, 500),
        )

    def convert(self, frame=None, profile=None, snapshot=None, channels=None):
        return intents_from_frame(
            self.frame if frame is None else frame,
            self.channels if channels is None else channels,
            self.gate,
            profile=self.profile if profile is None else profile,
            snapshot=self.snapshot if snapshot is None else snapshot,
        )

    def test_maps_bilateral_waveform_to_gate_validated_virtual_intents(self):
        intents = self.convert()
        self.assertEqual([intent.joint for intent in intents], ["left_hip", "right_hip"])
        self.assertEqual([intent.revision for intent in intents], [3, 3])
        for intent in intents:
            self.assertEqual(self.gate.validate(intent), intent.target)

    def test_small_positive_and_negative_outputs_round_toward_zero_symmetrically(self):
        profile = CPGProfile(
            revision=3,
            tick_us=1_000,
            minimum_frequency_millihz=500,
            base_frequency_millihz=500,
            maximum_frequency_millihz=500,
            amplitude_milli=1_000,
            coupling_gain_milli=100,
            sensory_phase_gain=50,
            maximum_phase_correction=100,
        )
        cpg = BilateralCPG(profile)
        frame = cpg.step(SensoryFeedback(tick=1, profile_revision=3))
        snapshot = cpg.snapshot()
        self.assertEqual(frame.left_output_milli, 2)
        self.assertEqual(frame.right_output_milli, -2)
        gate = MotorGate(
            {"left_hip": (-1.0, 1.0), "right_hip": (-1.0, 1.0)},
            revision=3,
        )
        intents = intents_from_frame(
            frame,
            (
                JointChannel("left_hip", "left", 0, 250),
                JointChannel("right_hip", "right", 0, 250),
            ),
            gate,
            profile=profile,
            snapshot=snapshot,
        )
        self.assertEqual([intent.target for intent in intents], [0.0, 0.0])

    def test_rejects_stale_frame_revision(self):
        with self.assertRaisesRegex(CPGBridgeError, "revision"):
            self.convert(frame=replace(self.frame, profile_revision=2))

    def test_rejects_non_synthetic_or_actuation_frame(self):
        with self.assertRaises(CPGBridgeError):
            self.convert(frame=replace(self.frame, physical_actuation=True))

    def test_rejects_bool_waveform_values(self):
        with self.assertRaisesRegex(CPGBridgeError, "left_output"):
            self.convert(frame=replace(self.frame, left_output_milli=True))

    def test_rejects_altered_in_range_output_even_with_original_digest(self):
        altered = replace(self.frame, left_output_milli=0)
        with self.assertRaisesRegex(CPGBridgeError, "outputs do not match"):
            self.convert(frame=altered)

    def test_rejects_frame_phase_not_bound_to_snapshot(self):
        altered = replace(
            self.frame,
            left_phase_microturns=(self.frame.left_phase_microturns + 1) % 1_000_000,
        )
        with self.assertRaisesRegex(CPGBridgeError, "verified CPG snapshot"):
            self.convert(frame=altered)

    def test_rejects_snapshot_with_invalid_state_digest(self):
        altered = replace(self.snapshot, left_phase_microturns=1)
        with self.assertRaisesRegex(CPGBridgeError, "snapshot"):
            self.convert(snapshot=altered)

    def test_rejects_mismatched_profile(self):
        other = replace(self.profile, amplitude_milli=900)
        with self.assertRaisesRegex(CPGBridgeError, "profile digest"):
            self.convert(profile=other)

    def test_rejects_non_native_phase_and_frequency_fields(self):
        with self.assertRaisesRegex(CPGBridgeError, "left_phase"):
            self.convert(frame=replace(self.frame, left_phase_microturns=True))
        with self.assertRaisesRegex(CPGBridgeError, "frequency"):
            self.convert(frame=replace(self.frame, frequency_millihz="1000"))

    def test_rejects_duplicate_joint_mapping(self):
        with self.assertRaisesRegex(CPGBridgeError, "duplicate"):
            self.convert(channels=(self.channels[0], self.channels[0]))

    def test_motor_gate_is_authoritative_for_joint_limits(self):
        channels = (JointChannel("left_hip", "left", 2_000, 0),)
        with self.assertRaisesRegex(CPGBridgeError, "MotorGate rejected"):
            self.convert(channels=channels)

    def test_estop_remains_latched_and_blocks_conversion(self):
        self.gate.trip_estop()
        with self.assertRaisesRegex(CPGBridgeError, "MotorGate rejected"):
            self.convert()
        self.assertTrue(self.gate.estop_latched)

    def test_feedback_builder_is_bounded_and_explicitly_synthetic(self):
        feedback = feedback_from_loads(
            tick=2,
            profile_revision=3,
            left_load_milli=250,
            right_load_milli=750,
            frequency_delta_millihz=100,
        )
        self.assertIs(feedback.synthetic, True)
        self.assertEqual(feedback.tick, 2)
        with self.assertRaises(CPGBridgeError):
            feedback_from_loads(
                tick=2,
                profile_revision=3,
                left_load_milli=True,
                right_load_milli=0,
            )


if __name__ == "__main__":
    unittest.main()
