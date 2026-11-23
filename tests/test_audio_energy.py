import math
import struct
import unittest

from mojum.audio_energy import (
    PCM_BYTES, SCHEMA, measure_pcm16_frame,
)


class AudioEnergyFrameTests(unittest.TestCase):
    def test_silence_is_not_speech(self):
        e = measure_pcm16_frame(bytes(PCM_BYTES), observed_at_ns=40_000_000)
        self.assertEqual(e["schema"], SCHEMA)
        self.assertEqual(e["duration_ns"], 20_000_000)
        self.assertEqual(e["rms_normalized"], 0.0)
        self.assertFalse(e["energy_gate_open"])
        self.assertFalse(e["raw_audio_retained"])
        self.assertEqual(e["classification"], "energy-only-not-speech")
        self.assertNotIn("transcript", e)

    def test_strong_signed_pcm_has_energy(self):
        pcm = struct.pack("<320h", *([16384, -16384] * 160))
        e = measure_pcm16_frame(pcm, observed_at_ns=2,
                                clock="monotonic", origin="microphone-opt-in")
        self.assertAlmostEqual(e["rms_normalized"], 0.5)
        self.assertAlmostEqual(e["peak_normalized"], 0.5)
        self.assertTrue(e["energy_gate_open"])

    def test_small_signal_below_threshold(self):
        pcm = struct.pack("<320h", *([512] * 320))
        self.assertFalse(measure_pcm16_frame(pcm, observed_at_ns=0)["energy_gate_open"])

    def test_rejects_wrong_frame_size_or_type(self):
        for pcm in (b"", bytes(638), bytes(642), bytearray(PCM_BYTES),
                    memoryview(bytes(PCM_BYTES)), None):
            with self.subTest(sample_type=str(type(pcm))):
                with self.assertRaises(ValueError):
                    measure_pcm16_frame(pcm, observed_at_ns=0)

    def test_rejects_untrusted_clock_and_provenance(self):
        for clock, origin in (("wall", "synthetic"), ("simulated", "microphone-opt-in"),
                              ("monotonic", "real"), ("simulated", "mic")):
            with self.subTest(clock=clock, origin=origin):
                with self.assertRaises(ValueError):
                    measure_pcm16_frame(bytes(PCM_BYTES), observed_at_ns=0,
                                        clock=clock, origin=origin)

    def test_rejects_bad_timestamps_and_energy_gates(self):
        for t in (True, -1, 2**63, 1.2, "0"):
            with self.assertRaises(ValueError):
                measure_pcm16_frame(bytes(PCM_BYTES), observed_at_ns=t)
        for th in (True, 0, -1, 1.1, float("nan"), float("inf"), 10**1000):
            with self.assertRaises(ValueError):
                measure_pcm16_frame(bytes(PCM_BYTES), observed_at_ns=0, threshold=th)

    def test_per_frame_measures_are_repeatable(self):
        frame = struct.pack("<320h", *range(320))
        self.assertEqual(
            measure_pcm16_frame(frame, observed_at_ns=123),
            measure_pcm16_frame(frame, observed_at_ns=123),
        )


if __name__ == "__main__":
    unittest.main()
