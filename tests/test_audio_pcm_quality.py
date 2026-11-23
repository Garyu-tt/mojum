"""Hermetic offline tests for opt-in PCM quality analysis."""
import json
import struct
import unittest

from mojum.audio_pcm_quality import (
    FRAME_BYTES,
    MAX_BYTES,
    SCHEMA,
    analyze_pcm16_quality,
)


def pcm(samples):
    return struct.pack("<" + "h" * len(samples), *samples)


def quality(data, **changes):
    args = {"sample_rate_hz": 16000, "origin": "PUBLIC_SYNTHETIC", "enabled": True}
    args.update(changes)
    return analyze_pcm16_quality(data, **args)


class PCMQualityTests(unittest.TestCase):
    def test_requires_opt_in(self):
        with self.assertRaisesRegex(ValueError, "opt-in"):
            analyze_pcm16_quality(bytes(FRAME_BYTES), sample_rate_hz=16000, origin="PUBLIC_SYNTHETIC")
        for value in (0, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quality(bytes(FRAME_BYTES), enabled=value)

    def test_rejects_private_or_forged_origin(self):
        for origin in ("PRIVATE", "microphone-opt-in", "public_synthetic", "PUBLIC_SYNTHETIC\\0", b"PUBLIC_SYNTHETIC", None):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                quality(bytes(FRAME_BYTES), origin=origin)

    def test_rejects_untrusted_pcm_types(self):
        class BytesSubclass(bytes):
            pass
        for data in (bytearray(FRAME_BYTES), memoryview(bytes(FRAME_BYTES)), BytesSubclass(bytes(FRAME_BYTES)), "x" * FRAME_BYTES, None):
            with self.subTest(kind=type(data).__name__), self.assertRaises(ValueError):
                quality(data)

    def test_sample_rate_exact_type(self):
        for rate in (False, True, 8000, 48000, 16000.0, "16000", None):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                quality(bytes(FRAME_BYTES), sample_rate_hz=rate)

    def test_rejects_empty_truncated_and_oversized_before_decode(self):
        for data in (b"", b"\\0", bytes(FRAME_BYTES - 2), bytes(FRAME_BYTES + 2), bytes(MAX_BYTES + FRAME_BYTES)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                quality(data)

    def test_silence_is_not_dc_or_speech(self):
        out = quality(bytes(FRAME_BYTES))
        self.assertEqual(out["schema"], SCHEMA)
        self.assertEqual(out["sample_count"], 320)
        self.assertEqual(out["duration_ns"], 20_000_000)
        self.assertEqual(out["flags"], ["digital_silence"])
        self.assertEqual(out["zero_crossings"], 0)
        self.assertFalse(out["raw_audio_retained"])
        self.assertIn("not-speech", out["classification"])
        self.assertNotIn("pcm", out)

    def test_constant_dc_flatline(self):
        out = quality(pcm([2048] * 320))
        self.assertEqual(out["dc_mean_lsb"], 2048)
        self.assertEqual(out["flags"], ["flatline", "possible_dc_offset"])
        self.assertEqual(out["rail_hit_count"], 0)

    def test_alternating_signs(self):
        out = quality(pcm([-1000, 1000] * 160))
        self.assertEqual(out["zero_crossings"], 319)
        self.assertEqual(out["dc_mean_lsb"], 0)
        self.assertEqual(out["flags"], [])

    def test_zeros_do_not_hide_polarity_transitions(self):
        data = [-1, 0, 0, 1, 0, -1, 0, 1] + [0] * 312
        out = quality(pcm(data))
        self.assertEqual(out["zero_crossings"], 3)
        self.assertEqual(out["flags"], [])

    def test_adjacent_rails_suggest_clipping_not_proof(self):
        out = quality(pcm([0] * 316 + [32767] * 4))
        self.assertEqual(out["rail_hit_count"], 4)
        self.assertEqual(out["longest_identical_rail_run"], 4)
        self.assertEqual(out["flags"], ["possible_hard_clipping"])

    def test_isolated_rails_are_not_detected_as_clipped(self):
        out = quality(pcm([32767, -32768] * 160))
        self.assertEqual(out["rail_hit_count"], 320)
        self.assertEqual(out["longest_identical_rail_run"], 1)
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["dc_mean_lsb"], -1)  # -0.5 ties away from zero

    def test_negative_dc_and_negative_rail(self):
        out = quality(pcm([-32768] * 3 + [-4096] * 317))
        self.assertEqual(out["longest_identical_rail_run"], 3)
        self.assertIn("possible_dc_offset", out["flags"])
        self.assertIn("possible_hard_clipping", out["flags"])
        self.assertEqual(out["min_sample"], -32768)

    def test_exact_threshold_at_two_percent(self):
        # 320 samples: total=209715 is just below 2% of 320*32768,
        # total=209716 is >= threshold (2% * 10485760 / 100).
        below = [655] * 319 + [770]  # 209715
        above = [655] * 319 + [771]  # 209716
        self.assertNotIn("possible_dc_offset", quality(pcm(below))["flags"])
        self.assertIn("possible_dc_offset", quality(pcm(above))["flags"])

    def test_deterministic_serialization_and_zero_retention(self):
        data = pcm([123, -321] * 160)
        first, second = quality(data), quality(data)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertFalse(any(isinstance(v, bytes) for v in first.values()))

    def test_maximum_ten_second_budget(self):
        out = quality(bytes(MAX_BYTES))
        self.assertEqual(out["frame_count"], 500)
        self.assertEqual(out["sample_count"], 160_000)
        self.assertEqual(out["duration_ns"], 10_000_000_000)


if __name__ == "__main__":
    unittest.main()
