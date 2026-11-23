"""No sherpa download/model/real microphone required."""
import io
import unittest
import wave
from types import SimpleNamespace

from mojum.sherpa_offline_asr import (
    ASRBoundaryError, transcribe_public_wav, MAX_SAMPLES,
)


def wav(samples=100, *, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(b"\0" * samples * channels * width)
    return buf.getvalue()


class Stream:
    def __init__(self, text="hello"):
        self.result = SimpleNamespace(text=text)
        self.accepted = None

    def accept_waveform(self, rate, samples):
        self.accepted = (rate, samples)


class Recognizer:
    def __init__(self, text="hello"):
        self.calls = 0
        self.stream = Stream(text)

    def create_stream(self):
        self.calls += 1
        return self.stream

    def decode_stream(self, stream):
        assert stream is self.stream


class SherpaOfflineTests(unittest.TestCase):
    def test_identical_public_fixture_is_deterministic(self):
        packet = wav()
        a, b = Recognizer(), Recognizer()
        result = transcribe_public_wav(packet, a, enabled=True, visibility="PUBLIC_SYNTHETIC")
        self.assertEqual(result, transcribe_public_wav(packet, b, enabled=True, visibility="PUBLIC_SYNTHETIC"))
        self.assertEqual(result["frames"], 100)
        self.assertEqual(result["duration_ms"], 6)
        self.assertEqual(result["sample_rate"], 16000)
        self.assertEqual(result["visibility"], "PUBLIC_SYNTHETIC")
        self.assertEqual(result["transcript"], "hello")
        self.assertEqual(len(result["pcm_sha256"]), 64)
        self.assertFalse(result["physical_actuation"])
        self.assertEqual(a.stream.accepted[0], 16000)
        self.assertEqual(len(a.stream.accepted[1]), 100)
        self.assertAlmostEqual(float(sum(a.stream.accepted[1])), 0)
        self.assertNotIn("audio", str(result))

    def test_independent_mock_inference_and_full_frame_budget(self):
        packet = wav(16000)
        result = transcribe_public_wav(packet, Recognizer("synthetic"), enabled=True, visibility="PUBLIC_SYNTHETIC")
        self.assertEqual(result["duration_ms"], 1000)
        self.assertEqual(result["transcript"], "synthetic")

    def test_default_install_without_numpy_still_runs_synthetic_fixture(self):
        import sys
        from unittest.mock import patch
        with patch.dict(sys.modules, {"numpy": None}):
            result = transcribe_public_wav(wav(32), Recognizer(""), enabled=True, visibility="PUBLIC_SYNTHETIC")
        self.assertEqual(result["frames"], 32)

    def test_opt_in_and_private_denied_before_backend(self):
        a = Recognizer()
        with self.assertRaisesRegex(ASRBoundaryError, "opt-in"):
            transcribe_public_wav(wav(), a)
        # Opt-in alone never classifies unknown input as public synthetic.
        with self.assertRaisesRegex(ASRBoundaryError, "explicit PUBLIC_SYNTHETIC"):
            transcribe_public_wav(wav(), a, enabled=True)
        for visibility in ("PRIVATE", "PUBLIC", None, True, "PUBLIC_SYNTHETIC\n"):
            with self.subTest(visibility=visibility), self.assertRaises(ASRBoundaryError):
                transcribe_public_wav(wav(), a, enabled=True, visibility=visibility)
        self.assertEqual(a.calls, 0)

    def test_untrusted_wav_rejected(self):
        for data in (b"", b"hello", b"x" * 400001, wav(rate=44100),
                     wav(channels=2), wav(width=1), wav(samples=0),
                     wav(samples=MAX_SAMPLES + 1), wav()[:30],
                     wav()[:-17], "not bytes"):
            with self.subTest(kind=type(data), length=len(data)), self.assertRaises(ASRBoundaryError):
                transcribe_public_wav(data, Recognizer(), enabled=True, visibility="PUBLIC_SYNTHETIC")

    def test_transcript_contract(self):
        for value in (None, True, 3, "x" * 501, "\r", "hello\x00", "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ASRBoundaryError):
                transcribe_public_wav(wav(), Recognizer(value), enabled=True, visibility="PUBLIC_SYNTHETIC")
        self.assertEqual(transcribe_public_wav(wav(), Recognizer(""), enabled=True, visibility="PUBLIC_SYNTHETIC")["transcript"], "")

    def test_backend_failures_do_not_expose_private_diagnostics(self):
        marker = "token=fixture-secret /private/model/path"

        def explode(*_args):
            raise RuntimeError(marker)

        class ExplodingProperty:
            @property
            def create_stream(self):
                explode()

            def decode_stream(self, stream):
                return None

        class ExplodingResult:
            def accept_waveform(self, rate, samples):
                return None

            @property
            def result(self):
                explode()

        backends = (
            SimpleNamespace(create_stream=explode, decode_stream=lambda stream: None),
            SimpleNamespace(
                create_stream=lambda: SimpleNamespace(accept_waveform=explode),
                decode_stream=lambda stream: None,
            ),
            SimpleNamespace(create_stream=lambda: Stream(), decode_stream=explode),
            SimpleNamespace(
                create_stream=lambda: ExplodingResult(),
                decode_stream=lambda stream: None,
            ),
            ExplodingProperty(),
        )
        for backend in backends:
            with self.subTest(backend=type(backend).__name__):
                with self.assertRaises(ASRBoundaryError) as caught:
                    transcribe_public_wav(wav(), backend, enabled=True, visibility="PUBLIC_SYNTHETIC")
                self.assertEqual(str(caught.exception), "offline ASR backend failed")
                self.assertIsNone(caught.exception.__context__)

    def test_invalid_recognizer_denied(self):
        with self.assertRaises(ASRBoundaryError):
            transcribe_public_wav(wav(), object(), enabled=True, visibility="PUBLIC_SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
