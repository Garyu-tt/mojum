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
    return cpg.step(SensoryFeedback(tick=1, profile_revision=3))


class CPGBridgeTests(unittest.TestCase):
    def setUp(self):
        self.gate = MotorGate(
            {"left_hip": (-1.0, 1.0), "right_hip": (-1.0, 1.0)},
            revision=3,
        )
        self.frame = make_frame()
        self.channels = (
            JointChannel("left_hip", "left", 0, 500),
            JointChannel("right_hip", "right", 0, 500),
        )

    def test_maps_bilateral_waveform_to_gate_validated_virtual_intents(self):
        intents = intents_from_frame(self.frame, self.channels, self.gate)
        self.assertEqual([intent.joint for intent in intents], ["left_hip", "right_hip"])
        self.assertEqual([intent.revision for intent in intents], [3, 3])
        for intent in intents:
            self.assertEqual(self.gate.validate(intent), intent.target)

    def test_rejects_stale_frame_revision(self):
        with self.assertRaisesRegex(CPGBridgeError, "revision"):
            intents_from_frame(
                replace(self.frame, profile_revision=2),
                self.channels,
                self.gate,
            )

    def test_rejects_non_synthetic_or_actuation_frame(self):
        with self.assertRaises(CPGBridgeError):
            intents_from_frame(
                replace(self.frame, physical_actuation=True),
                self.channels,
                self.gate,
            )

    def test_rejects_bool_waveform_values(self):
        with self.assertRaisesRegex(CPGBridgeError, "left_output"):
            intents_from_frame(
                replace(self.frame, left_output_milli=True),
                self.channels,
                self.gate,
            )

    def test_rejects_duplicate_joint_mapping(self):
        with self.assertRaisesRegex(CPGBridgeError, "duplicate"):
            intents_from_frame(
                self.frame,
                (self.channels[0], self.channels[0]),
                self.gate,
            )

    def test_motor_gate_is_authoritative_for_joint_limits(self):
        channels = (
            JointChannel("left_hip", "left", 2_000, 0),
        )
        with self.assertRaisesRegex(CPGBridgeError, "MotorGate rejected"):
            intents_from_frame(self.frame, channels, self.gate)

    def test_estop_remains_latched_and_blocks_conversion(self):
        self.gate.trip_estop()
        with self.assertRaisesRegex(CPGBridgeError, "MotorGate rejected"):
            intents_from_frame(self.frame, self.channels, self.gate)
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
