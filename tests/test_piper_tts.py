"""Deterministic stdlib tests. Fake CLI != actual Piper inference."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from mojum.piper_tts import (
    MAX_TEXT_BYTES, MAX_WAV_BYTES, PiperProfile, SCHEMA, synthesize_public_wav,
)


class PiperTTSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.model = root / "synthetic.onnx"
        self.config = root / "synthetic.onnx.json"
        self.model.write_bytes(b"PUBLIC SYNTHETIC FAKE MODEL")
        self.config.write_bytes(b'{"fake":true}')
        self.script = root / "fake_python"
        self._script("ok")

    def _script(self, mode):
        program = f'''#!{sys.executable}
import os, sys, time, wave
if '-c' in sys.argv:
    installed = '0.0.0' if {mode!r} == 'version' else '1.6.1'
    if {mode!r} == 'loudversion':
        sys.stdout.write('x' * 1_000_000)
    raise SystemExit(0 if installed == sys.argv[-1] else 12)
if {mode!r} == 'error':
    raise SystemExit(4)
if {mode!r} == 'timeout':
    time.sleep(3)
if 'PIPER_TEST_SECRET' in os.environ:
    raise SystemExit(9)
if '--input-file' not in sys.argv or '-f' not in sys.argv:
    raise SystemExit(10)
text_path = sys.argv[sys.argv.index('--input-file') + 1]
text = open(text_path, encoding='utf-8').read()
if any(text.strip() == arg for arg in sys.argv):
    raise SystemExit(11)
path = sys.argv[sys.argv.index('-f') + 1]
if {mode!r} == 'bad':
    open(path, 'wb').write(b'not a wav')
elif {mode!r} == 'huge':
    open(path, 'wb').write(b'x' * {MAX_WAV_BYTES + 1})
elif {mode!r} == 'symlink':
    os.symlink(text_path, path)
else:
    with wave.open(path, 'wb') as out:
        out.setnchannels(2 if {mode!r} == 'stereo' else 1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b'\\x00\\x01' * (16 if {mode!r} != 'stereo' else 32))
'''
        self.script.write_text(program, encoding="utf-8")
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def _profile(self, **kwargs):
        fields = dict(
            python_executable=str(self.script.resolve()),
            python_sha256=hashlib.sha256(self.script.read_bytes()).hexdigest(),
            model_onnx=str(self.model.resolve()),
            model_sha256=hashlib.sha256(self.model.read_bytes()).hexdigest(),
            config_sha256=hashlib.sha256(self.config.read_bytes()).hexdigest(),
            expected_piper_version="1.6.1", voice_license_reviewed=True,
        )
        fields.update(kwargs)
        return PiperProfile(**fields)

    def test_off_by_default_and_privacy_gate_before_file_reads(self):
        p = self._profile(model_onnx="/nonexistent/model.onnx")
        with self.assertRaises(PermissionError):
            synthesize_public_wav("hello", p)
        with self.assertRaises(PermissionError):
            synthesize_public_wav("hello", p, enabled=True, provenance="PRIVATE")

    def test_provenance_selector_must_be_exact_string(self):
        class ForgedProvenance:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        with self.assertRaises(PermissionError):
            synthesize_public_wav("hello", self._profile(), enabled=True,
                                  provenance=ForgedProvenance())
        with self.assertRaises(PermissionError):
            synthesize_public_wav("hello", self._profile(), enabled=True,
                                  provenance=type("StrAlias", (str,), {})("PUBLIC_SYNTHETIC"))

    def test_valid_fake_wav_and_pinned_provenance(self):
        out = synthesize_public_wav("Привет, Данте", self._profile(), enabled=True)
        self.assertEqual(out.schema, SCHEMA)
        self.assertEqual(out.backend_version, "1.6.1")
        self.assertEqual(out.sample_rate_hz, 16000)
        self.assertEqual(out.sample_frames, 16)
        self.assertEqual(out.wav_sha256, hashlib.sha256(out.wav).hexdigest())
        self.assertGreater(out.elapsed_ms, -1)
        self.assertFalse(out.audio_played)
        self.assertNotIn(b"\xd0\x9f\xd1\x80", out.wav)

    def test_normalized_reproducible_wav(self):
        a = synthesize_public_wav("hello", self._profile(), enabled=True)
        b = synthesize_public_wav("hello", self._profile(), enabled=True)
        self.assertEqual(a.wav, b.wav)
        self.assertEqual(a.wav_sha256, b.wav_sha256)

    def test_no_unreviewed_model_allowed(self):
        with self.assertRaises(PermissionError):
            synthesize_public_wav("hi", self._profile(voice_license_reviewed=False), enabled=True)

    def test_invalid_public_text(self):
        for bad in ("", " ", "x\ny", "\x00", "\ud800", "e\u0301", "[[ p ]]", "x" * (MAX_TEXT_BYTES + 1), 7):
            with self.subTest(bad=repr(bad)[:20]), self.assertRaises(ValueError):
                synthesize_public_wav(bad, self._profile(), enabled=True)

    def test_bad_hashes_prevent_launch(self):
        for field in ("python_sha256", "model_sha256", "config_sha256"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                synthesize_public_wav("hi", self._profile(**{field: "0" * 64}), enabled=True)

    def test_reject_symlinked_model_and_missing_config(self):
        alias = self.model.parent / "alias.onnx"
        alias.symlink_to(self.model)
        with self.assertRaises(ValueError):
            synthesize_public_wav("hi", self._profile(model_onnx=str(alias)), enabled=True)
        profile = self._profile()
        self.config.unlink()
        with self.assertRaises(ValueError):
            synthesize_public_wav("hi", profile, enabled=True)

    def test_virtualenv_style_interpreter_symlink(self):
        alias = self.script.parent / "python-venv"
        alias.symlink_to(self.script)
        out = synthesize_public_wav("hi", self._profile(python_executable=str(alias)), enabled=True)
        self.assertEqual(out.sample_frames, 16)

    def test_package_version_mismatch(self):
        self._script("version")
        with self.assertRaises(RuntimeError):
            synthesize_public_wav("hi", self._profile(), enabled=True)

    def test_nonzero_exit(self):
        self._script("error")
        with self.assertRaises(RuntimeError):
            synthesize_public_wav("hi", self._profile(), enabled=True)

    def test_oversized_version_stdout_cannot_exhaust_capture(self):
        # Broken/pinned child process emits excessive data despite valid metadata.
        # The adapter must not capture it into parent memory or reject by size.
        self._script("loudversion")
        out = synthesize_public_wav("hi", self._profile(), enabled=True)
        self.assertEqual(out.sample_frames, 16)


    def test_process_timeout(self):
        self._script("timeout")
        with self.assertRaises(RuntimeError):
            synthesize_public_wav("hi", self._profile(), enabled=True, timeout_seconds=1)

    def test_malformed_or_unsafe_wav_rejected(self):
        for mode in ("bad", "stereo", "huge", "symlink"):
            self._script(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                synthesize_public_wav("hi", self._profile(), enabled=True)

    def test_reject_invalid_time_and_cpu_budget(self):
        for timeout in (-1, 0, 61, True, 2.5):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                synthesize_public_wav("hi", self._profile(), enabled=True, timeout_seconds=timeout)
        for threads in (0, 5, True, 1.5):
            with self.subTest(threads=threads), self.assertRaises(ValueError):
                synthesize_public_wav("hi", self._profile(), enabled=True, cpu_threads=threads)

    def test_do_not_inherit_secrets(self):
        old = os.environ.get("PIPER_TEST_SECRET")
        os.environ["PIPER_TEST_SECRET"] = "secret-do-not-pass"
        try:
            result = synthesize_public_wav("test", self._profile(), enabled=True)
        finally:
            if old is None:
                del os.environ["PIPER_TEST_SECRET"]
            else:
                os.environ["PIPER_TEST_SECRET"] = old
        self.assertEqual(result.sample_frames, 16)


if __name__ == "__main__":
    unittest.main()
