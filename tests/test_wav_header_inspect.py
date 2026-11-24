"""Offline stdlib regression tests for WAV inspection."""
import struct
import unittest

from mojum.wav_header_inspect import inspect_wav, WavFormatError, MAX_WAV_BYTES


def chunk(kind, payload):
    return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def wav(payload=b"\x00\x00", rate=16000, extra=b""):
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + chunk(b"fmt ", fmt) + extra + chunk(b"data", payload)
    return b"RIFF" + struct.pack("<I", len(body)) + body


class WavInspectionTests(unittest.TestCase):
    def test_valid(self):
        report = inspect_wav(wav(b"\x00\x00" * 160))
        self.assertEqual(report["duration_ms_floor"], 10)
        self.assertEqual(report["sample_count"], 160)
        self.assertNotIn("samples", report)

    def test_empty_audio(self):
        self.assertEqual(inspect_wav(wav(b""))["sample_count"], 0)

    def test_ancillary_odd_chunk(self):
        report = inspect_wav(wav(extra=chunk(b"JUNK", b"x")))
        self.assertEqual(report["chunk_count"], 3)

    def test_bad_types(self):
        for value in (bytearray(wav()), memoryview(wav()), "", None):
            with self.subTest(value=type(value)):
                with self.assertRaises(TypeError):
                    inspect_wav(value)

    def test_corrupt_lengths(self):
        original = wav()
        for value in (original[:-1], original + b"x", original[:4] + b"\x00\x00\x00\x00" + original[8:]):
            with self.subTest(length=len(value)):
                with self.assertRaises(WavFormatError):
                    inspect_wav(value)

    def test_invalid_formats(self):
        for rate in (7999, 192001):
            with self.assertRaises(WavFormatError):
                inspect_wav(wav(rate=rate))
        with self.assertRaises(WavFormatError):
            inspect_wav(wav(b"x"))

    def test_duplicate_chunks(self):
        with self.assertRaises(WavFormatError):
            inspect_wav(wav(extra=chunk(b"data", b"")))

    def test_nonzero_padding(self):
        invalid = wav(extra=chunk(b"JUNK", b"x").replace(b"x\x00", b"x\x01"))
        with self.assertRaises(WavFormatError):
            inspect_wav(invalid)

    def test_budget(self):
        with self.assertRaises(WavFormatError):
            inspect_wav(b"x" * (MAX_WAV_BYTES + 1))

    def test_no_fmt(self):
        body = b"WAVE" + chunk(b"data", b"")
        with self.assertRaises(WavFormatError):
            inspect_wav(b"RIFF" + struct.pack("<I", len(body)) + body)


if __name__ == "__main__":
    unittest.main()
