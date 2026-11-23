"""Hermetic CPU-only invariants for opt-in PCM16 mono -> 16k conversion."""

import hashlib
import math
import struct
import unittest

from mojum.audio_pcm_resample import (
    MAX_SECONDS, PCMResampleError, SCHEMA, SOURCE_TAG, TARGET_RATE,
    resample_pcm16le_mono,
)


def encode(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def decode(payload):
    return struct.unpack(f"<{len(payload) // 2}h", payload)


class PCMResampleTests(unittest.TestCase):
    def convert(self, payload, rate=48000):
        return resample_pcm16le_mono(payload, rate, opt_in=True,
                                     source_tag=SOURCE_TAG)

    def test_requires_explicit_opt_in_before_any_conversion(self):
        for consent in (False, None, 1, "yes"):
            with self.subTest(consent=consent), self.assertRaises(PCMResampleError):
                resample_pcm16le_mono(encode([0, 0, 0]), 48000,
                                      opt_in=consent)

    def test_source_tag_must_be_explicit(self):
        # A missing label must not silently assert PUBLIC_SYNTHETIC provenance.
        payload = encode([100, 200, 300])
        with self.assertRaises(PCMResampleError):
            resample_pcm16le_mono(payload, 48000, opt_in=True)
        with self.assertRaises(PCMResampleError):
            resample_pcm16le_mono(payload, 48000, opt_in=True, source_tag=None)
        tagged = resample_pcm16le_mono(
            payload, 48000, opt_in=True, source_tag=SOURCE_TAG)
        self.assertEqual(tagged.source_tag, SOURCE_TAG)

    def test_rejects_forged_source_tag_and_subclasses(self):
        class ForgedStr(str):
            def __eq__(self, other):
                return True

        for source in ("PRIVATE", ForgedStr(SOURCE_TAG), 7):
            with self.subTest(source=source), self.assertRaises(PCMResampleError):
                resample_pcm16le_mono(encode([0, 0, 0]), 48000,
                                      opt_in=True, source_tag=source)

    def test_rejects_hostile_payload_and_rate_types(self):
        class BytesChild(bytes):
            pass

        class RateChild(int):
            pass

        for payload in (bytearray(b"\x00\x00"), memoryview(b"\x00\x00"),
                        BytesChild(b"\x00\x00"), [], "\x00\x00"):
            with self.subTest(payload=type(payload)), self.assertRaises(PCMResampleError):
                self.convert(payload, 16000)
        for rate in (True, 16000.0, RateChild(16000), 44100, 0, -48000):
            with self.subTest(rate=rate), self.assertRaises(PCMResampleError):
                self.convert(b"\x00\x00", rate)

    def test_rejects_partial_sample_empty_and_incomplete_decimation(self):
        for payload, rate in ((b"", 16000), (b"\x01", 16000),
                              (encode([1]), 32000),
                              (encode([1, 2, 3, 4]), 48000)):
            with self.subTest(rate=rate), self.assertRaises(PCMResampleError):
                self.convert(payload, rate)

    def test_pass_through_preserves_signed_little_endian_bytes(self):
        payload = encode([-32768, -1000, 0, 1, 32767])
        result = self.convert(payload, 16000)
        self.assertEqual(result.pcm16le, payload)
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(result.output_sample_rate_hz, TARGET_RATE)
        self.assertEqual(result.source_frames, 5)
        self.assertEqual(result.output_frames, 5)
        self.assertEqual(result.sha256_hex, hashlib.sha256(payload).hexdigest())

    def test_silence_is_exact_for_all_rates(self):
        for rate in (16000, 32000, 48000):
            with self.subTest(rate=rate):
                out = self.convert(bytes(rate // 100 * 2), rate)
                self.assertEqual(out.pcm16le, bytes(320))
                self.assertEqual(out.output_frames, 160)

    def test_constant_signed_level_no_edge_transient(self):
        for rate in (32000, 48000):
            for level in (-32768, -1234, 1234, 32767):
                with self.subTest(rate=rate, level=level):
                    result = self.convert(encode([level] * (rate // 100)), rate)
                    self.assertEqual(decode(result.pcm16le), (level,) * 160)

    def test_explicit_input_size_budget_before_unpack(self):
        with self.assertRaises(PCMResampleError):
            self.convert(bytes(48000 * MAX_SECONDS * 2 + 2), 48000)
        self.assertEqual(self.convert(bytes(48000 * 2), 48000).output_frames, 16000)

    def test_rate_provenance_and_exact_output_length(self):
        for rate in (16000, 32000, 48000):
            with self.subTest(rate=rate):
                result = self.convert(encode([1] * (rate // 100)), rate)
                self.assertEqual(result.source_sample_rate_hz, rate)
                self.assertEqual(result.output_sample_rate_hz, TARGET_RATE)
                self.assertEqual(result.source_frames, rate // 100)
                self.assertEqual(result.output_frames, 160)
                self.assertEqual(len(result.pcm16le), 320)
                self.assertEqual(len(result.sha256_hex), 64)

    def test_passband_one_khz_preserved_both_rates(self):
        for rate in (32000, 48000):
            with self.subTest(rate=rate):
                data = encode([int(12000 * math.sin(2 * math.pi * 1000 * n / rate))
                               for n in range(rate // 5)])
                samples = decode(self.convert(data, rate).pcm16le)
                # Ignore FIR boundary transients at start/end.
                interior = samples[100:-100]
                amplitude = (max(interior) - min(interior)) / 2
                self.assertGreater(amplitude, 11700)
                self.assertLess(amplitude, 12300)

    def test_stopband_ten_khz_attenuated_both_rates(self):
        for rate in (32000, 48000):
            with self.subTest(rate=rate):
                data = encode([int(12000 * math.sin(2 * math.pi * 10000 * n / rate))
                               for n in range(rate // 5)])
                interior = decode(self.convert(data, rate).pcm16le)[100:-100]
                # FIR >=~55 dB theoretical; allow margin for PCM quantization.
                self.assertLess(max(abs(v) for v in interior), 50)

    def test_replay_determinism_and_immutable_result(self):
        pattern = encode([((i * 3023) % 65536) - 32768 for i in range(480)])
        first, second = self.convert(pattern), self.convert(pattern)
        self.assertEqual(first, second)
        self.assertEqual(first.sha256_hex, hashlib.sha256(first.pcm16le).hexdigest())
        # Pinned cross-version output witnesses: these depend on the signed
        # Q20 taps, edge extension, clipping and integer rounding contract.
        self.assertEqual(first.sha256_hex,
                         "64e38b1a531cc3439e8a5625efe79d097a4dba8bde45f59b6e84f5c8cfd9f7c3")
        self.assertEqual(self.convert(pattern, 32000).sha256_hex,
                         "cc632c3a9391b70c1dd5aaba533c5809fd2f057ffb23caa8f5bd75fbb1619e39")
        with self.assertRaises((AttributeError, TypeError)):
            first.output_frames = 0


if __name__ == "__main__":
    unittest.main()
