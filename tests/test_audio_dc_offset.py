import hashlib
import math
import random
import struct
import unittest

from mojum.audio_dc_offset import (
    CLASSIFICATION, MAX_SAMPLES, SAMPLE_RATE_HZ, SCHEMA,
    inspect_pcm16_dc_offset,
)


def report(samples):
    return inspect_pcm16_dc_offset(
        struct.pack("<" + str(len(samples)) + "h", *samples),
        classification=CLASSIFICATION,
    )


class DCOffsetDiagnosticsTests(unittest.TestCase):
    def test_silence(self):
        r = report([0] * 320)
        self.assertEqual(r["schema"], SCHEMA)
        self.assertEqual(r["duration_ns"], 20_000_000)
        self.assertEqual(r["samples"], 320)
        for key in ("dc_mean_normalized", "rms_normalized",
                    "ac_rms_normalized", "dc_fraction_of_rms"):
            self.assertEqual(r[key], 0.0)
        self.assertEqual(r["zero_samples"], 320)
        self.assertEqual(r["clipped_samples"], 0)

    def test_constant_dc_has_zero_ac_energy(self):
        r = report([16384] * 320)
        self.assertEqual(r["sum_samples"], 320 * 16384)
        self.assertEqual(r["dc_mean_normalized"], 0.5)
        self.assertEqual(r["rms_normalized"], 0.5)
        self.assertEqual(r["ac_rms_normalized"], 0.0)
        self.assertEqual(r["dc_fraction_of_rms"], 1.0)

    def test_balanced_ac_has_no_dc_offset(self):
        r = report([-8192, 8192] * 160)
        self.assertEqual(r["dc_mean_normalized"], 0.0)
        self.assertEqual(r["dc_fraction_of_rms"], 0.0)
        self.assertEqual(r["rms_normalized"], 0.25)
        self.assertEqual(r["ac_rms_normalized"], 0.25)

    def test_intermediate_mixture_matches_exact_variance(self):
        r = report([0, 0, 2000, 2000])
        self.assertEqual(r["sum_samples"], 4000)
        self.assertEqual(r["sum_squares"], 8000000)
        self.assertEqual(r["ac_rms_normalized"], 1000 / 32768)
        self.assertAlmostEqual(r["rms_normalized"], math.sqrt(2000000) / 32768)
        self.assertAlmostEqual(r["dc_fraction_of_rms"], 1 / math.sqrt(2))

    def test_signed_extremes_count_as_clipping(self):
        r = report([-32768, 32767, -32767, 32766])
        self.assertEqual(r["clipped_samples"], 2)
        self.assertEqual((r["sample_min"], r["sample_max"]), (-32768, 32767))
        self.assertEqual(r["samples"], 4)
        self.assertEqual(r["sum_samples"], -2)

    def test_single_sample_is_valid(self):
        r = report([-32768])
        self.assertEqual(r["samples"], 1)
        self.assertEqual(r["duration_ns"], 62500)
        self.assertEqual(r["clipped_samples"], 1)
        self.assertEqual(r["dc_mean_normalized"], -1)
        self.assertEqual(r["ac_rms_normalized"], 0)

    def test_maximum_budget_and_over_budget(self):
        r = inspect_pcm16_dc_offset(
            bytes(MAX_SAMPLES * 2), classification=CLASSIFICATION)
        self.assertEqual(r["samples"], MAX_SAMPLES)
        self.assertEqual(r["duration_ns"], 10_000_000_000)
        with self.assertRaisesRegex(ValueError, "whole PCM16"):
            inspect_pcm16_dc_offset(
                bytes((MAX_SAMPLES + 1) * 2), classification=CLASSIFICATION)

    def test_rejects_empty_odd_and_non_bytes(self):
        for payload in (b"", b"\x00", b"\x00\x00\x00",
                        bytearray(b"\0\0"), memoryview(b"\0\0"),
                        [0], None, "00"):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(ValueError):
                    inspect_pcm16_dc_offset(payload, classification=CLASSIFICATION)

    def test_rejects_implicit_classification(self):
        for label in (None, "", "synthetic", "PRIVATE", "PUBLIC",
                      True, ["PUBLIC_SYNTHETIC"]):
            with self.subTest(label=repr(label)):
                with self.assertRaises(ValueError):
                    inspect_pcm16_dc_offset(b"\0\0", classification=label)
        with self.assertRaises(TypeError):
            inspect_pcm16_dc_offset(b"\0\0")

    def test_rejects_inexact_sampling_rate(self):
        for rate in (True, 16000.0, "16000", 8000, 48000, None):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    inspect_pcm16_dc_offset(b"\0\0",
                                            classification=CLASSIFICATION,
                                            sample_rate_hz=rate)

    def test_result_is_detached_redacted_and_hash_pinned(self):
        pcm = struct.pack("<4h", 0, 128, -128, 0)
        a = inspect_pcm16_dc_offset(pcm, classification=CLASSIFICATION)
        b = inspect_pcm16_dc_offset(pcm, classification=CLASSIFICATION)
        self.assertEqual(a, b)
        self.assertEqual(a["pcm_sha256"], hashlib.sha256(pcm).hexdigest())
        self.assertTrue(a["diagnostic_only"])
        self.assertFalse(a["raw_audio_retained"])
        self.assertEqual(a["classification"], CLASSIFICATION)
        self.assertNotIn("raw_pcm", a)
        self.assertNotIn("waveform", a)
        self.assertNotIn("transcript", a)
        a["samples"] = -1
        self.assertEqual(b["samples"], 4)
        self.assertNotEqual(b["pcm_sha256"], report([0, 0, 0, 0])["pcm_sha256"])

    def test_small_deterministic_random_corpus(self):
        rng = random.Random(965)
        for n in (1, 2, 7, 320, 1024):
            vals = [rng.randint(-32768, 32767) for _ in range(n)]
            r = report(vals)
            self.assertEqual(r["sum_samples"], sum(vals))
            self.assertEqual(r["sum_squares"], sum(x*x for x in vals))
            self.assertEqual(r["clipped_samples"],
                             sum(x in (-32768, 32767) for x in vals))
            self.assertLessEqual(r["ac_rms_normalized"],
                                 r["rms_normalized"] + 1e-15)
            self.assertTrue(all(math.isfinite(r[key]) for key in
                            ("rms_normalized", "ac_rms_normalized",
                             "dc_mean_normalized", "dc_fraction_of_rms")))
            self.assertGreaterEqual(r["dc_fraction_of_rms"], 0)
            self.assertLessEqual(r["dc_fraction_of_rms"], 1)


if __name__ == "__main__":
    unittest.main()
