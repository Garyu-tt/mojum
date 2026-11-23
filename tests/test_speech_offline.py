"""No native ASR/model/audio hardware needed for deterministic boundary QA."""
import json
import unittest
from unittest.mock import Mock

from mojum.speech_offline import (
    MAX_WINDOW_SAMPLES, OfflineSpeechSession, SAMPLE_RATE_HZ,
    SCHEMA, SherpaOnnxOffline, SpeechBridgeError,
)


class FakeStream:
    def __init__(self):
        self.calls = []
        self.result = Mock(text="  привет, Данте  ")

    def accept_waveform(self, sample_rate, samples):
        self.calls.append((sample_rate, list(samples)))


class FakeRecognizer:
    def __init__(self):
        self.calls = 0
        self.stream = FakeStream()

    def create_stream(self):
        self.calls += 1
        return self.stream

    def decode_stream(self, stream):
        assert stream is self.stream


class SpeechOfflineTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = FakeRecognizer()
        self.backend = SherpaOnnxOffline(self.recognizer, waveform_factory=list)
        self.session = OfflineSpeechSession(self.backend, model_id="test_sherpa_2.0.3", enabled=True)
        self.kw = dict(sample_rate_hz=SAMPLE_RATE_HZ, start_ms=100, language_hint="ru", consent=True)

    def test_real_sherpa_method_contract_and_event(self):
        event = self.session.transcribe([0.2] * 160, **self.kw)
        self.assertEqual(event["schema"], SCHEMA)
        self.assertEqual((event["sequence"], event["start_ms"], event["end_ms"]), (0, 100, 110))
        self.assertEqual((event["text"], event["status"]), ("привет, Данте", "recognized"))
        self.assertEqual((event["is_final"], event["privacy"]), (True, "PRIVATE_not_persisted"))
        self.assertEqual(self.recognizer.stream.calls, [(16000, [0.2] * 160)])
        self.assertNotIn("audio", json.dumps(event))
        self.assertNotIn("waveform", event)
        self.assertNotIn("confidence", event)

    def test_opt_in_and_consent_before_backend(self):
        disabled = OfflineSpeechSession(self.backend, model_id="test")
        with self.assertRaises(SpeechBridgeError):
            disabled.transcribe([0.2], **self.kw)
        with self.assertRaises(SpeechBridgeError):
            self.session.transcribe([0.2], **{**self.kw, "consent": False})
        self.assertEqual(self.recognizer.calls, 0)

    def test_silence_suppresses_model(self):
        event = self.session.transcribe([0.0] * 16000, **self.kw)
        self.assertEqual((event["text"], event["status"]), ("", "silence"))
        self.assertEqual(event["end_ms"], 1100)
        self.assertEqual(self.recognizer.calls, 0)

    def test_consecutive_non_overlapping_windows(self):
        self.session.transcribe([0.2] * 160, **self.kw)
        next_event = self.session.transcribe([0.2] * 160, **{**self.kw, "start_ms": 110})
        self.assertEqual((next_event["sequence"], next_event["end_ms"]), (1, 120))
        with self.assertRaises(SpeechBridgeError):
            self.session.transcribe([0.2], **{**self.kw, "start_ms": 119})
        self.assertEqual(self.recognizer.calls, 2)

    def test_bad_pcm_fails_before_native_backend(self):
        cases = ([], [float("nan")], [float("inf")], [1.01], [-1.01], [True], ["0.2"], [0.0] * (MAX_WINDOW_SAMPLES + 1), b"raw")
        for samples in cases:
            with self.subTest(samples=str(samples[:3]) if isinstance(samples, list) else samples):
                with self.assertRaises(SpeechBridgeError):
                    self.session.transcribe(samples, **self.kw)
        self.assertEqual(self.recognizer.calls, 0)

    def test_rate_language_timestamp_and_model_id_validation(self):
        for kwargs in ({"sample_rate_hz": True}, {"sample_rate_hz": 44100},
                       {"language_hint": "ru; rm -rf"}, {"language_hint": "RU"},
                       {"start_ms": -1}, {"start_ms": True},
                       {"start_ms": 2**53 - 1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SpeechBridgeError):
                    self.session.transcribe([0.2] * 160, **{**self.kw, **kwargs})
        for model_id in ("/home/private/model", "token secret", "", "x" * 65):
            with self.assertRaises(SpeechBridgeError):
                OfflineSpeechSession(self.backend, model_id=model_id, enabled=True)
        self.assertEqual(self.recognizer.calls, 0)

    def test_backend_failure_does_not_advance_cursor(self):
        self.recognizer.decode_stream = Mock(side_effect=RuntimeError("private model path"))
        with self.assertRaisesRegex(SpeechBridgeError, "offline ASR backend failed") as ctx:
            self.session.transcribe([0.2] * 160, **self.kw)
        self.assertNotIn("private", str(ctx.exception))
        self.recognizer.decode_stream = Mock(return_value=None)
        success = self.session.transcribe([0.2] * 160, **self.kw)
        self.assertEqual(success["sequence"], 0)

    def test_backend_unsafe_text_rolls_back(self):
        for transcript in ("x" * 1025, "unsafe\ntext", "\x00bad", None):
            with self.subTest(transcript=repr(transcript)[:16]):
                self.recognizer.stream.result.text = transcript
                with self.assertRaises(SpeechBridgeError):
                    self.session.transcribe([0.2] * 160, **self.kw)
        self.recognizer.stream.result.text = "ok"
        self.assertEqual(self.session.transcribe([0.2] * 160, **self.kw)["sequence"], 0)

    def test_no_speech_model_output_is_distinct_from_silence(self):
        self.recognizer.stream.result.text = "  "
        event = self.session.transcribe([0.3] * 160, **self.kw)
        self.assertEqual(event["status"], "no_speech")
        self.assertEqual(self.recognizer.calls, 1)


if __name__ == "__main__":
    unittest.main()
