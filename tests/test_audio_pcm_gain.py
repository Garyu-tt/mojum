"""Hermetic stdlib tests of the isolated public-synthetic PCM gain helper."""
import dataclasses
import struct
import unittest

from mojum.audio_pcm_gain import (
    AudioGainError,
    MAX_BYTES,
    MAX_DURATION_SECONDS,
    MAX_GAIN,
    SCHEMA,
    VISIBILITY,
    apply_pcm16_gain,
)


def pcm(*values: int) -> bytes:
    return struct.pack("<" + "h" * len(values), *values)


def samples(raw: bytes) -> tuple[int, ...]:
    return tuple(item[0] for item in struct.iter_unpack("<h", raw))


class PCM16GainTests(unittest.TestCase):
    def call(self, raw=pcm(1), **overrides):
        opts = {"sample_rate_hz": 16000, "gain_numerator": 1, "visibility": VISIBILITY}
        opts.update(overrides)
        return apply_pcm16_gain(raw, **opts)

    def test_identity_is_byte_exact_and_metadata_only_repr(self):
        raw = pcm(-32768, -123, 0, 123, 32767)
        output = self.call(raw)
        self.assertEqual(output.pcm, raw)
        self.assertEqual(output.schema, SCHEMA)
        self.assertEqual(output.visibility, VISIBILITY)
        self.assertEqual(output.frames, 5)
        self.assertEqual(output.input_peak_abs, 32768)
        self.assertEqual(output.output_peak_abs, 32768)
        self.assertEqual(output.changed_samples, 0)
        self.assertEqual(output.clipped_samples, 0)
        self.assertNotIn("pcm=", repr(output))

    def test_gain_zero_is_silent_without_division_bug(self):
        result = self.call(pcm(11, -32768, 32767), gain_numerator=0)
        self.assertEqual(samples(result.pcm), (0, 0, 0))
        self.assertEqual(result.changed_samples, 3)
        self.assertEqual(result.clipped_samples, 0)
        self.assertEqual(result.output_peak_abs, 0)

    def test_gain_and_negative_sign(self):
        result = self.call(pcm(-1500, -1, 0, 1, 1500), gain_numerator=3, gain_denominator=2)
        self.assertEqual(samples(result.pcm), (-2250, -1, 0, 1, 2250))
        self.assertEqual(result.changed_samples, 2)
        self.assertEqual(result.clipped_samples, 0)

    def test_half_ties_round_toward_zero(self):
        result = self.call(pcm(-5, -3, -1, 1, 3, 5), gain_numerator=1, gain_denominator=2)
        self.assertEqual(samples(result.pcm), (-2, -1, 0, 0, 1, 2))

    def test_more_than_half_rounds_away_from_zero(self):
        result = self.call(pcm(-3, -2, -1, 1, 2, 3), gain_numerator=2, gain_denominator=3)
        self.assertEqual(samples(result.pcm), (-2, -1, -1, 1, 1, 2))

    def test_saturation_distinguishes_negative_rail(self):
        result = self.call(pcm(-32768, -16384, -1, 0, 16384, 32767), gain_numerator=2)
        self.assertEqual(samples(result.pcm), (-32768, -32768, -2, 0, 32767, 32767))
        self.assertEqual(result.clipped_samples, 3)
        self.assertEqual(result.changed_samples, 3)
        self.assertEqual(result.output_peak_abs, 32768)

    def test_exact_rail_is_not_clipped(self):
        result = self.call(pcm(-16384, 1000), gain_numerator=2)
        self.assertEqual(samples(result.pcm), (-32768, 2000))
        self.assertEqual(result.clipped_samples, 0)

    def test_extreme_legal_gain(self):
        result = self.call(pcm(1, -1, 8191, -8192), gain_numerator=MAX_GAIN)
        self.assertEqual(samples(result.pcm), (4, -4, 32764, -32768))
        self.assertEqual(result.clipped_samples, 0)

    def test_rate_is_preserved(self):
        for rate in (8000, 16000, 32000, 48000):
            with self.subTest(rate=rate):
                result = self.call(pcm(-1, 1), sample_rate_hz=rate)
                self.assertEqual((result.sample_rate_hz, result.frames), (rate, 2))

    def test_reject_non_native_or_untrusted_pcm(self):
        class BytesChild(bytes):
            pass
        for raw in (None, "\x00\x00", bytearray(b"\x00\x00"), memoryview(b"\x00\x00"), BytesChild(b"\x00\x00"), b"", b"\x01"):
            with self.subTest(raw_type=str(type(raw))):
                with self.assertRaises(AudioGainError):
                    self.call(raw)

    def test_reject_unsupported_rates_including_bool(self):
        for rate in (0, -1, 44100, True, 16000.0, "16000", 10**999):
            with self.subTest(rate=str(rate)[:16]):
                with self.assertRaises(AudioGainError):
                    self.call(sample_rate_hz=rate)

    def test_reject_bad_rationals(self):
        for num, den in ((1, 0), (1, -1), (1, True), (1, 1.5), (1, 1_000_001),
                         (True, 1), (1.0, 1), ("1", 1), (-1, 1), (5, 1), (0, 0),
                         (10**999, 1)):
            with self.subTest(num=str(num)[:12], den=str(den)[:12]):
                with self.assertRaises(AudioGainError):
                    self.call(gain_numerator=num, gain_denominator=den)

    def test_omitted_visibility_fails_closed(self):
        with self.assertRaisesRegex(AudioGainError, "PUBLIC_SYNTHETIC"):
            apply_pcm16_gain(pcm(0), sample_rate_hz=16000,
                             gain_numerator=1)
        with self.assertRaises(AudioGainError):
            apply_pcm16_gain(pcm(0), sample_rate_hz=16000,
                             gain_numerator=1, visibility=None)
        accepted = apply_pcm16_gain(pcm(0), sample_rate_hz=16000,
                                    gain_numerator=1, visibility=VISIBILITY)
        self.assertEqual(accepted.pcm, pcm(0))

    def test_reject_forged_provenance(self):
        class StrChild(str):
            def __eq__(self, other):
                return True
        for visibility in ("PRIVATE", "public_synthetic", True, None, StrChild(VISIBILITY)):
            with self.subTest(type=str(type(visibility))):
                with self.assertRaises(AudioGainError):
                    self.call(visibility=visibility)

    def test_reject_size_before_decode(self):
        with self.assertRaises(AudioGainError):
            self.call(bytes(MAX_BYTES + 2), sample_rate_hz=48000)
        with self.assertRaises(AudioGainError):
            self.call(bytes(MAX_DURATION_SECONDS * 16000 * 2 + 2), sample_rate_hz=16000)

    def test_long_but_bounded_zero_fixture(self):
        raw = bytes(MAX_DURATION_SECONDS * 8000 * 2)
        result = self.call(raw, sample_rate_hz=8000, gain_numerator=2)
        self.assertEqual(result.pcm, raw)
        self.assertEqual(result.frames, 80000)
        self.assertEqual((result.changed_samples, result.clipped_samples), (0, 0))

    def test_determinism_and_result_immutability(self):
        raw = pcm(-32768, -7, -1, 1, 7, 32767)
        first = self.call(raw, gain_numerator=7, gain_denominator=3)
        self.assertEqual(first, self.call(raw, gain_numerator=7, gain_denominator=3))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.frames = 99


if __name__ == "__main__":
    unittest.main()
