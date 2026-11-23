import dataclasses
import struct
import unittest

from mojum.audio_pcm_downmix import (
    MAX_FRAMES, MAX_INPUT_BYTES, SCHEMA, downmix_stereo_pcm16le,
)


def stereo(*pairs):
    return b"".join(struct.pack("<hh", left, right) for left, right in pairs)


def mono(pcm):
    return tuple(sample[0] for sample in struct.iter_unpack("<h", pcm))


class PCM16DownmixTests(unittest.TestCase):
    def test_silence_preserves_frame_count_and_rate(self):
        result = downmix_stereo_pcm16le(bytes(320 * 4), sample_rate_hz=16_000)
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(result.pcm16le, bytes(640))
        self.assertEqual((result.frames, result.sample_rate_hz), (320, 16_000))
        self.assertEqual((result.input_channels, result.output_channels), (2, 1))
        self.assertEqual(result.visibility, "PUBLIC_SYNTHETIC")
        self.assertEqual(result.rounding, "integer-mean-toward-zero")

    def test_equal_channels_are_not_attenuated(self):
        result = downmix_stereo_pcm16le(stereo((-32768, -32768), (-7, -7),
                                                 (0, 0), (32767, 32767)),
                                          sample_rate_hz=48_000)
        self.assertEqual(mono(result.pcm16le), (-32768, -7, 0, 32767))
        self.assertEqual(result.sample_rate_hz, 48_000)

    def test_antiphase_cancels_and_extreme_sum_is_in_range(self):
        result = downmix_stereo_pcm16le(
            stereo((30000, -30000), (-32768, 32767),
                   (-32768, -32768), (32767, 32767)), sample_rate_hz=44_100)
        self.assertEqual(mono(result.pcm16le), (0, 0, -32768, 32767))

    def test_halves_round_toward_zero_not_negative_infinity(self):
        result = downmix_stereo_pcm16le(
            stereo((0, 1), (0, -1), (2, 1), (-2, -1), (32767, 0), (-32768, 0)),
            sample_rate_hz=8_000)
        self.assertEqual(mono(result.pcm16le), (0, 0, 1, -1, 16383, -16384))

    def test_rejects_non_bytes_without_copy_or_coercion(self):
        for value in (None, [], "test", bytearray(4), memoryview(bytes(4))):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    downmix_stereo_pcm16le(value, sample_rate_hz=16_000)

    def test_rejects_partial_and_empty_frames(self):
        for length in (0, 1, 2, 3, 5, 6, 7, 11):
            with self.subTest(length=length):
                with self.assertRaises(ValueError):
                    downmix_stereo_pcm16le(bytes(length), sample_rate_hz=16_000)

    def test_rejects_invalid_rates(self):
        for rate in (None, True, 8000.0, "16000", 0, -1, 7999, 192001, 2**100):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    downmix_stereo_pcm16le(bytes(4), sample_rate_hz=rate)

    def test_rejects_private_or_forged_visibility(self):
        for visibility in (None, True, "PRIVATE", "private", "PUBLIC", "synthetic"):
            with self.subTest(visibility=visibility):
                with self.assertRaises(ValueError):
                    downmix_stereo_pcm16le(bytes(4), sample_rate_hz=16_000,
                                           visibility=visibility)

    def test_duration_gate_uses_declared_rate(self):
        accepted = downmix_stereo_pcm16le(bytes(8_000 * 10 * 4), sample_rate_hz=8_000)
        self.assertEqual(accepted.frames, 80_000)
        with self.assertRaises(ValueError):
            downmix_stereo_pcm16le(bytes((8_000 * 10 + 1) * 4), sample_rate_hz=8_000)

    def test_maximum_byte_budget_and_output_length(self):
        self.assertEqual(MAX_FRAMES * 4, MAX_INPUT_BYTES)
        accepted = downmix_stereo_pcm16le(bytes(MAX_INPUT_BYTES), sample_rate_hz=16_000)
        self.assertEqual(accepted.frames, MAX_FRAMES)
        self.assertEqual(len(accepted.pcm16le), MAX_INPUT_BYTES // 2)
        with self.assertRaises(ValueError):
            downmix_stereo_pcm16le(bytes(MAX_INPUT_BYTES + 4), sample_rate_hz=16_000)

    def test_repeatable_and_immutable_result(self):
        source = stereo((123, 124), (-123, -124), (-100, 200))
        a = downmix_stereo_pcm16le(source, sample_rate_hz=22_050)
        b = downmix_stereo_pcm16le(source, sample_rate_hz=22_050)
        self.assertEqual(a, b)
        self.assertEqual(source, stereo((123, 124), (-123, -124), (-100, 200)))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            a.frames = 1

    def test_no_implicit_resampling_or_wav_header(self):
        source = stereo((100, 200), (300, 400), (500, 600))
        a = downmix_stereo_pcm16le(source, sample_rate_hz=16_000)
        b = downmix_stereo_pcm16le(source, sample_rate_hz=48_000)
        self.assertEqual(mono(a.pcm16le), (150, 350, 550))
        self.assertEqual(a.pcm16le, b.pcm16le)
        self.assertEqual(a.frames, b.frames)
        self.assertNotEqual(a.sample_rate_hz, b.sample_rate_hz)
        self.assertNotIn(b"RIFF", a.pcm16le)


if __name__ == "__main__":
    unittest.main()
