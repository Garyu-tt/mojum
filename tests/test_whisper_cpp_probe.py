"""No upstream dependency: fake trusted CLI exercises the exact subprocess boundary."""

import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest import mock
import wave

from mojum.whisper_cpp_probe import (
    AUDIO_SCHEMA, RESULT_SCHEMA, WhisperCPPProbeError, compare_transcript,
    run_whispercpp_probe,
)


def wav_fixture(*, channels=1, sample_rate=16000, width=2, frames=1600):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\0" * (frames * width * channels))
    return buffer.getvalue()


@unittest.skipIf(os.name == "nt", "POSIX fake executable fixture")
class WhisperCPPProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / "whisper-cli"
        self.model = self.root / "test-model.bin"
        self.model.write_bytes(b"synthetic-model-not-real-whisper-weights")
        self._fake_cli("""#!/bin/sh
out=''
gpu=0
text=0
threads=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -of) shift; out="$1" ;;
    -ng) gpu=1 ;;
    -otxt) text=1 ;;
    -t) shift; threads="$1" ;;
  esac
  shift
done
[ "$gpu" -eq 1 ] && [ "$text" -eq 1 ] && [ "$threads" -eq 2 ] && [ -n "$out" ] || exit 17
printf 'Hello world!\n' > "$out.txt"
""")
        self.audio = wav_fixture()
        self.manifest = {
            "schema": AUDIO_SCHEMA,
            "visibility": "PUBLIC",
            "origin": "synthetic_fixture",
            "clip_id": "synthetic.tone.01",
            "opt_in": True,
        }

    def _fake_cli(self, content):
        self.binary.write_text(content, encoding="utf-8")
        self.binary.chmod(0o700)

    def _run(self, **changes):
        args = {
            "wav_bytes": self.audio,
            "manifest": self.manifest,
            "enabled": True,
            "executable_path": self.binary,
            "model_path": self.model,
            "executable_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
            "backend_revision": "a" * 40,
            "language": "en",
            "threads": 2,
            "timeout_s": 3,
            "frozen_reference": "hello world!",
        }
        args.update(changes)
        return run_whispercpp_probe(**args)

    def test_positive_pinned_local_comparison(self):
        result = self._run()
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual(result["clip_id"], "synthetic.tone.01")
        self.assertEqual(result["transcript"], "Hello world!")
        self.assertEqual(result["frozen_reference_score"], {
            "reference_words": 2, "edit_distance": 0, "word_error_rate": 0,
        })
        self.assertEqual(result["frames"], 1600)
        self.assertEqual(result["duration_ms"], 100)
        self.assertIsNone(result["motor_action"])
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertNotIn(str(self.root), str(result))
        second = self._run()
        a = {k: v for k, v in result.items() if k != "elapsed_ms"}
        b = {k: v for k, v in second.items() if k != "elapsed_ms"}
        self.assertEqual(a, b)

    def test_no_spawn_without_explicit_opt_in(self):
        with self.assertRaisesRegex(WhisperCPPProbeError, "disabled"):
            self._run(enabled=False)
        with self.assertRaisesRegex(WhisperCPPProbeError, "disabled"):
            self._run(enabled=1)

    def test_private_or_untrusted_manifest_is_rejected(self):
        for update in (
            {"visibility": "PRIVATE"},
            {"visibility": "RESTRICTED"},
            {"origin": "microphone"},
            {"opt_in": False},
            {"schema": "unknown"},
            {"clip_id": "../../secret"},
            {"clip_id": "\ud800"},
        ):
            with self.subTest(update=repr(update)):
                with self.assertRaises(WhisperCPPProbeError):
                    self._run(manifest={**self.manifest, **update})
        with self.assertRaises(WhisperCPPProbeError):
            self._run(manifest={**self.manifest, "access_token": "secret"})

    def test_manifest_discriminators_require_actual_strings(self):
        class SpoofedStringInt(int):
            def __eq__(self, other):
                return other in (AUDIO_SCHEMA, "PUBLIC", "synthetic_fixture")
            def __ne__(self, other):
                return not self.__eq__(other)

        for key in ("schema", "visibility", "origin"):
            with self.subTest(key=key), self.assertRaisesRegex(
                WhisperCPPProbeError, "PUBLIC synthetic fixtures"
            ):
                self._run(manifest={**self.manifest, key: SpoofedStringInt(7)})

    def test_private_staging_survives_mutation_before_spawn(self):
        # A file may be atomically replaced after a SHA preflight. The child
        # must use the already-verified, private copies, never source paths.
        original_run = subprocess.run
        def mutate_and_run(args, **kwargs):
            self.assertNotEqual(Path(args[0]), self.binary)
            self.assertNotEqual(Path(args[2]), self.model)
            self.assertEqual(Path(args[0]).stat().st_mode & 0o777, 0o700)
            self.assertEqual(Path(args[2]).stat().st_mode & 0o777, 0o600)
            self._fake_cli("#!/bin/sh\nexit 91\n")
            self.model.write_bytes(b"mutated-unpinned-model")
            return original_run(args, **kwargs)

        with mock.patch("mojum.whisper_cpp_probe.subprocess.run", side_effect=mutate_and_run):
            result = self._run()
        self.assertEqual(result["transcript"], "Hello world!")
        self.assertEqual(result["frozen_reference_score"]["word_error_rate"], 0)

    def test_pcm16_mono_16k_and_duration_enforced(self):
        for candidate in (
            wav_fixture(channels=2),
            wav_fixture(sample_rate=8000),
            wav_fixture(width=1),
            wav_fixture(frames=0),
            wav_fixture(frames=160001),
            b"bad", self.audio[:-2], self.audio + b"extra",
        ):
            with self.subTest(length=len(candidate)):
                with self.assertRaises(WhisperCPPProbeError):
                    self._run(wav_bytes=candidate)
        with self.assertRaises(WhisperCPPProbeError):
            self._run(wav_bytes=bytearray(self.audio))

    def test_binary_model_pins_and_path_gate(self):
        with self.assertRaisesRegex(WhisperCPPProbeError, "SHA-256 mismatch"):
            self._run(executable_sha256="0" * 64)
        with self.assertRaisesRegex(WhisperCPPProbeError, "SHA-256 mismatch"):
            self._run(model_sha256="0" * 64)
        with self.assertRaises(WhisperCPPProbeError):
            self._run(backend_revision="unversioned")
        with self.assertRaises(WhisperCPPProbeError):
            self._run(executable_path=Path("whisper-cli"))
        alias = self.root / "whisper-link"
        alias.symlink_to(self.binary)
        with self.assertRaises(WhisperCPPProbeError):
            self._run(executable_path=alias)

    def test_bounds_fail_closed(self):
        for changes in (
            {"threads": True}, {"threads": 0}, {"threads": 9},
            {"timeout_s": float("inf")}, {"timeout_s": float("nan")},
            {"timeout_s": True}, {"timeout_s": 121},
            {"language": "auto"}, {"language": "--output-json"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(WhisperCPPProbeError):
                    self._run(**changes)

    def test_nonzero_missing_oversize_and_invalid_utf8_output(self):
        for script in (
            "#!/bin/sh\nexit 19\n",
            "#!/bin/sh\nexit 0\n",
            "#!/bin/sh\nout=''\nwhile [ \"$#\" -gt 0 ]; do\n  if [ \"$1\" = \"-of\" ]; then shift; out=\"$1\"; fi\n  shift\ndone\nprintf '\\377' > \"$out.txt\"\n",
        ):
            with self.subTest(script=script):
                self._fake_cli(script)
                with self.assertRaises(WhisperCPPProbeError):
                    self._run()
        self._fake_cli("""#!/bin/sh
out=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-of" ]; then shift; out="$1"; fi
  shift
done
i=0
: > "$out.txt"
while [ "$i" -lt 5000 ]; do
  printf 'A' >> "$out.txt"
  i=$((i + 1))
done
""")
        with self.assertRaisesRegex(WhisperCPPProbeError, "budget exceeded"):
            self._run()

    def test_timeout_is_bounded(self):
        self._fake_cli("#!/bin/sh\n/bin/sleep 4\n")
        with self.assertRaisesRegex(WhisperCPPProbeError, "timed out"):
            self._run(timeout_s=1)

    def test_transcript_comparison_is_standalone(self):
        self.assertEqual(compare_transcript("Hello world", "hello mars"), {
            "reference_words": 2, "edit_distance": 1, "word_error_rate": .5,
        })
        self.assertEqual(compare_transcript("one two", "")["word_error_rate"], 1)
        with self.assertRaises(WhisperCPPProbeError):
            compare_transcript("", "hi")
        with self.assertRaises(WhisperCPPProbeError):
            compare_transcript("x " * 129, "y")
        with self.assertRaises(WhisperCPPProbeError):
            compare_transcript("hi", "\x00private")


if __name__ == "__main__":
    unittest.main()
