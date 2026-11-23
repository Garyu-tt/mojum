"""Adversarial, offline tests for the opt-in TTS adapter contract."""
import hashlib
import unittest

from mojum.audio_tts import (
    MAX_CHUNK_BYTES,
    RESULT_SCHEMA,
    TTSBackendError,
    TTSError,
    TTSRequest,
    synthesize,
    validate_request,
)


class FakeBackend:
    backend_id = "fake.local"
    backend_version = "1.2.0"
    local_only = True

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = 0

    def iter_pcm16le(self, request):
        self.calls += 1
        yield from self.chunks


def request(**changes):
    fields = dict(
        text="Hello, Dante.",
        language="en",
        voice_id="preset.neutral",
        sample_rate_hz=24_000,
        max_audio_ms=10,
        content_class="PUBLIC_SYNTHETIC",
        consent=True,
    )
    fields.update(changes)
    return TTSRequest(**fields)


class TTSContractTests(unittest.TestCase):
    def test_valid_request_and_result_metadata(self):
        pcm = b"\x01\x00" * 120
        backend = FakeBackend([pcm[:100], pcm[100:]])
        result = synthesize(request(), backend)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(result.schema, RESULT_SCHEMA)
        self.assertEqual(result.backend_id, "fake.local")
        self.assertEqual(result.sample_rate_hz, 24_000)
        self.assertEqual(result.channels, 1)
        self.assertEqual(result.frames, 120)
        self.assertEqual(result.duration_ms, 5)
        self.assertEqual(result.pcm16le, pcm)
        self.assertEqual(result.sha256, hashlib.sha256(pcm).hexdigest())

    def test_deterministic_result_digest(self):
        pcm = b"\x00\x00" * 24
        a = synthesize(request(), FakeBackend([pcm]))
        b = synthesize(request(), FakeBackend([pcm]))
        self.assertEqual(a, b)

    def test_explicit_consent_required_before_backend_invocation(self):
        backend = FakeBackend([b"\x00\x00" * 24])
        with self.assertRaisesRegex(TTSError, "consent"):
            request(consent=False)
        self.assertEqual(backend.calls, 0)

    def test_unknown_or_private_content_class_rejected(self):
        for value in ("PRIVATE", "RESTRICTED", "private", "SYSTEM"):
            with self.subTest(value=value), self.assertRaises(TTSError):
                request(content_class=value)

    def test_exact_native_types_required(self):
        invalid = (
            {"consent": 1},
            {"sample_rate_hz": True},
            {"max_audio_ms": True},
            {"text": type("Text", (str,), {})("hello")},
            {"voice_id": type("Voice", (str,), {})("preset")},
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(TTSError):
                request(**fields)

    def test_text_must_be_bounded_utf8_without_nul(self):
        for value in ("", "a\x00b", "x" * 2_001):
            with self.subTest(value=value[:10]), self.assertRaises(TTSError):
                request(text=value)

    def test_language_tag_and_voice_id_are_constrained(self):
        for fields in (
            {"language": "en_US"},
            {"language": "not a tag"},
            {"language": "x" * 36},
            {"voice_id": "../outside"},
            {"voice_id": "voice with spaces"},
        ):
            with self.subTest(fields=fields), self.assertRaises(TTSError):
                request(**fields)

    def test_only_supported_sample_rates_and_duration_budgets(self):
        for fields in (
            {"sample_rate_hz": 8_000},
            {"sample_rate_hz": 44_100},
            {"sample_rate_hz": True},
            {"max_audio_ms": 0},
            {"max_audio_ms": 30_001},
            {"max_audio_ms": False},
        ):
            with self.subTest(fields=fields), self.assertRaises(TTSError):
                request(**fields)

    def test_hostile_request_subclass_is_rejected_by_entrypoint(self):
        class ChildRequest(TTSRequest):
            pass
        obj = object.__new__(ChildRequest)
        for field, value in request().__dict__.items():
            object.__setattr__(obj, field, value)
        with self.assertRaisesRegex(TTSError, "exact TTSRequest"):
            validate_request(obj)

    def test_backend_must_declare_local_only(self):
        class Remote(FakeBackend):
            local_only = False
        backend = Remote([b"\x00\x00" * 24])
        with self.assertRaisesRegex(TTSError, "local_only"):
            synthesize(request(), backend)
        self.assertEqual(backend.calls, 0)

    def test_missing_or_malformed_backend_metadata_rejected(self):
        class Missing:
            def iter_pcm16le(self, req):
                yield b"\x00\x00" * 24
        with self.assertRaises(TTSError):
            synthesize(request(), Missing())

        class Malformed(FakeBackend):
            backend_id = type("Id", (str,), {})("fake")
        with self.assertRaisesRegex(TTSError, "backend_id"):
            synthesize(request(), Malformed([]))

    def test_backend_without_stream_method_rejected(self):
        class NoStream:
            backend_id = "fake"
            backend_version = "1"
            local_only = True
        with self.assertRaisesRegex(TTSError, "iter_pcm16le"):
            synthesize(request(), NoStream())

    def test_materialized_backend_list_is_rejected(self):
        class Materialized:
            backend_id = "fake"
            backend_version = "1"
            local_only = True
            def iter_pcm16le(self, req):
                return [b"\x00\x00" * 24]
        with self.assertRaisesRegex(TTSError, "streaming iterator"):
            synthesize(request(), Materialized())

    def test_empty_output_rejected(self):
        with self.assertRaisesRegex(TTSError, "no audio"):
            synthesize(request(), FakeBackend([]))

    def test_non_bytes_chunk_rejected(self):
        with self.assertRaisesRegex(TTSError, "exact bytes"):
            synthesize(request(), FakeBackend([bytearray(b"\x00\x00")]))

    def test_odd_length_pcm_chunk_rejected(self):
        with self.assertRaisesRegex(TTSError, "incomplete sample"):
            synthesize(request(), FakeBackend([b"\x00"]))

    def test_oversized_chunk_rejected(self):
        with self.assertRaisesRegex(TTSError, "per-chunk"):
            synthesize(request(max_audio_ms=30_000),
                       FakeBackend([b"\x00" * (MAX_CHUNK_BYTES + 2)]))

    def test_total_duration_budget_enforced_incrementally(self):
        # At 24 kHz and 1 ms, no more than 48 bytes are admissible.
        backend = FakeBackend([b"\x00\x00" * 20, b"\x00\x00" * 5])
        with self.assertRaisesRegex(TTSError, "duration"):
            synthesize(request(max_audio_ms=1), backend)

    def test_chunk_count_budget_enforced(self):
        def too_many():
            for _ in range(4_097):
                yield b""
        backend = FakeBackend(too_many())
        with self.assertRaisesRegex(TTSError, "chunk-count"):
            synthesize(request(), backend)

    def test_backend_exception_is_normalized(self):
        class Broken(FakeBackend):
            def iter_pcm16le(self, req):
                yield b"\x00\x00"
                raise RuntimeError("private backend detail")
        with self.assertRaisesRegex(TTSBackendError, "producing PCM") as caught:
            synthesize(request(), Broken([]))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertNotIn("private backend detail", str(caught.exception))

    def test_output_is_immutable_bytes_and_not_backend_buffer(self):
        source = bytearray(b"\x01\x00" * 24)
        result = synthesize(request(), FakeBackend([bytes(source)]))
        source[:] = b"\x00" * len(source)
        self.assertIs(type(result.pcm16le), bytes)
        self.assertEqual(result.pcm16le, b"\x01\x00" * 24)

    def test_request_validation_does_not_load_or_import_a_backend(self):
        value = request()
        validate_request(value)
        self.assertTrue(value.consent)


if __name__ == "__main__":
    unittest.main()
