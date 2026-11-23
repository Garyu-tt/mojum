"""Deterministic isolated tests of native-compatible VAD queue and privacy gates."""
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from mojum.sherpa_vad import MAX_SAMPLES, SCHEMA, WINDOW_SAMPLES, segment_public_pcm16


class _Vec:
    def __init__(self, values, *, ndim=1):
        self.values = list(values)
        self.ndim = ndim
        self.size = len(self.values)
    def copy(self):
        return _Vec(self.values, ndim=self.ndim)
    def astype(self, dtype):
        return _Vec(self.values, ndim=self.ndim)
    def __getitem__(self, key):
        x = self.values[key]
        return _Vec(x, ndim=self.ndim) if isinstance(key, slice) else x
    def __truediv__(self, value):
        return _Vec([x/value for x in self.values])
    def __abs__(self):
        return _Vec([abs(x) for x in self.values])
    def __gt__(self, x):
        return _Vec([v > x for v in self.values])
    def all(self):
        return all(self.values)


class _FakeNumpy:
    float32 = "float32"
    @staticmethod
    def frombuffer(raw, dtype):
        assert dtype == "<i2"
        return _Vec((x[0] for x in struct.iter_unpack("<h", raw)))
    @staticmethod
    def asarray(values):
        if isinstance(values, _Vec):
            return values.copy()
        if values and isinstance(values[0], (list, tuple)):
            return _Vec([v for row in values for v in row], ndim=2)
        return _Vec(values)
    @staticmethod
    def zeros(size, dtype=None):
        if isinstance(size, tuple):
            return _Vec([0] * (size[0]*size[1]), ndim=2)
        return _Vec([0] * size)
    @staticmethod
    def array(values):
        return _Vec(values)
    @staticmethod
    def isfinite(values):
        return _Vec(math.isfinite(x) for x in values.values)
    @staticmethod
    def abs(values):
        return abs(values)
    @staticmethod
    def any(values):
        return any(values.values)
    @staticmethod
    def all(values):
        return all(values.values)
    @staticmethod
    def pad(values, padding):
        return _Vec([0]*padding[0] + values.values + [0]*padding[1])


class _Config:
    def __init__(self):
        self.silero_vad = type("Silero", (), {"window_size": 512})()


class _Segment:
    def __init__(self, start, samples):
        self.start, self.samples = start, samples


class _FakeSherpa:
    """Only synthetic fakes; not an assertion of native-engine accuracy."""
    VadModelConfig = _Config

    def __init__(self, *, emitted=(), throw_at=None):
        self.emitted = list(emitted)
        self.throw_at = throw_at
        self.detector = None

    def VoiceActivityDetector(self, cfg, buffer_size_in_seconds):
        parent = self
        class D:
            def __init__(self):
                self.frames = []
                self.queue = []
                self.flushed = False
            def accept_waveform(self, values):
                if parent.throw_at == len(self.frames):
                    raise RuntimeError("SECRET TEST TRANSCRIPT")
                self.frames.append(values.copy())
            def empty(self):
                return not self.queue
            @property
            def front(self):
                return self.queue[0]
            def pop(self):
                self.queue.pop(0)
            def flush(self):
                self.flushed = True
                self.queue.extend(parent.emitted)
        self.detector = D()
        self.config = cfg
        self.buffer = buffer_size_in_seconds
        return self.detector


class SherpaVadTests(unittest.TestCase):
    def setUp(self):
        self.np = _FakeNumpy()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model = os.path.join(self.tmp.name, "silero_vad.onnx")
        Path(self.model).write_bytes(b"public synthetic fake model")
        self.sha = hashlib.sha256(Path(self.model).read_bytes()).hexdigest()
        self.pcm = struct.pack("<1024h", *([16384, -16384] * 512))

    def run_vad(self, **extra):
        opts = dict(pcm=self.pcm, observed_at_ns=1_000_000_000,
                    model_path=self.model, model_sha256=self.sha,
                    enabled=True, consent=True, _sherpa=_FakeSherpa(),
                    _numpy=self.np, _backend_version="2.0.3")
        opts.update(extra)
        return segment_public_pcm16(**opts)

    def test_native_shape_and_timestamp_projection(self):
        fake = _FakeSherpa(emitted=[_Segment(100, self.np.zeros(200, dtype="float32")),
                                    _Segment(600, self.np.zeros(300, dtype="float32"))])
        result = self.run_vad(_sherpa=fake)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["source_scope"], "PUBLIC_SYNTHETIC")
        self.assertEqual([(s["start_sample"], s["end_sample"]) for s in result["segments"]],
                         [(100, 300), (600, 900)])
        self.assertEqual(result["segments"][0]["start_at_ns"], 1_006_250_000)
        self.assertEqual(result["segments"][0]["end_at_ns"], 1_018_750_000)
        self.assertEqual(result["duration_ns"], 64_000_000)
        self.assertFalse(result["raw_audio_retained"])
        self.assertFalse(result["transcription_performed"])
        self.assertNotIn("transcript_text", str(result))
        self.assertEqual(len(fake.detector.frames), 2)
        self.assertTrue(fake.detector.flushed)
        self.assertEqual(fake.config.num_threads, 1)
        self.assertEqual(fake.config.provider, "cpu")
        self.assertEqual(fake.config.silero_vad.window_size, WINDOW_SAMPLES)
        self.assertAlmostEqual(fake.detector.frames[0][0], .5)

    def test_test_double_cannot_spoof_native_provenance(self):
        result = self.run_vad()
        self.assertEqual(result["backend"], "unverified-test-double-vad")
        self.assertNotEqual(result["backend"], "sherpa-onnx-silero-vad")
        with self.assertRaisesRegex(ValueError, "incomplete test-only"):
            self.run_vad(_sherpa=_FakeSherpa(), _numpy=None)
        with self.assertRaisesRegex(ValueError, "incomplete test-only"):
            self.run_vad(_backend_version=None)

    def test_repeatability_and_silence(self):
        self.assertEqual(self.run_vad(), self.run_vad())
        self.assertEqual(self.run_vad(pcm=bytes(1024))["segments"], [])

    def test_partial_final_window_is_zero_padded(self):
        fake = _FakeSherpa(emitted=[_Segment(510, self.np.zeros(100, dtype="float32"))])
        out = self.run_vad(pcm=self.pcm[:1200], _sherpa=fake)
        self.assertEqual(out["segments"][0]["end_sample"], 600)
        self.assertEqual(len(fake.detector.frames), 2)
        self.assertTrue(all(v == 0 for v in fake.detector.frames[1][88:].values))

    def test_no_consent_or_enabled_never_imports_engine_or_reads_model(self):
        for kw in ({"enabled":False}, {"consent":False}, {"enabled":1}, {"consent":1}):
            with self.subTest(kw=kw):
                with patch("mojum.sherpa_vad._verified_local_model") as read:
                    with self.assertRaises(PermissionError):
                        self.run_vad(**kw)
                    read.assert_not_called()

    def test_rejects_invalid_pcm_input(self):
        for pcm in (b"", b"\x00", b"\x00" * 1000 + b"z", bytearray(self.pcm),
                    bytes((MAX_SAMPLES + 1) * 2)):
            with self.subTest(size=len(pcm)):
                with self.assertRaises(ValueError):
                    self.run_vad(pcm=pcm)

    def test_invalid_timestamps(self):
        for t in (True, -1, "1", (1 << 63)-1):
            with self.assertRaises(ValueError):
                self.run_vad(observed_at_ns=t)

    def test_wrong_path_symlink_digest_and_bounds_fail_before_engine(self):
        for path, digest in (("silero_vad.onnx", self.sha),
                             (self.model, "x"*64), (self.model, self.sha.upper()),
                             (self.model, "0"*64)):
            with self.assertRaises(ValueError):
                self.run_vad(model_path=path, model_sha256=digest)
        symlink = os.path.join(self.tmp.name, "link.onnx")
        os.symlink(self.model, symlink)
        with self.assertRaises(ValueError):
            self.run_vad(model_path=symlink)

    def test_wrong_backend_version_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unverified"):
            self.run_vad(_backend_version="1.0.0")

    def test_native_failure_scrubs_internal_exception(self):
        with self.assertRaisesRegex(RuntimeError, "no observations committed") as caught:
            self.run_vad(_sherpa=_FakeSherpa(throw_at=1))
        self.assertNotIn("SECRET", str(caught.exception))

    def test_native_reordered_overlapping_and_out_of_range_segments(self):
        cases = [
            [_Segment(300, self.np.zeros(100)), _Segment(200, self.np.zeros(100))],
            [_Segment(100, self.np.zeros(200)), _Segment(200, self.np.zeros(100))],
            [_Segment(9999, self.np.zeros(100))],
            [_Segment(0, self.np.array([float("nan")]))],
            [_Segment(0, self.np.zeros((2, 2)))],
            [_Segment(True, self.np.zeros(100))],
            [_Segment(0, self.np.array([2.0]))],
        ]
        for segments in cases:
            with self.subTest(segments=len(segments)):
                with self.assertRaisesRegex(RuntimeError, "no observations committed"):
                    self.run_vad(_sherpa=_FakeSherpa(emitted=segments))

    def test_too_many_segments_bounded(self):
        segments = [_Segment(i*2, self.np.zeros(1)) for i in range(65)]
        with self.assertRaisesRegex(RuntimeError, "no observations committed"):
            self.run_vad(_sherpa=_FakeSherpa(emitted=segments))

    def test_missing_optional_packages_are_explicit(self):
        with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "optional sherpa"):
                self.run_vad(_sherpa=None, _numpy=None)


if __name__ == "__main__":
    unittest.main()
