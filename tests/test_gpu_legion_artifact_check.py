"""Tests for the read-only model artifact integrity gate, without real models."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

# The 3.13 leak audit discovers tests with tests/ as sys.path[0].
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import gpu_legion_artifact_check as artifact


class ArtifactCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = b"GGUF" + b"\x00\x01\x02\x03" * 137
        self.file = self.root / "model.gguf"
        self.file.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_exact_artifact_receipt_not_runtime_claim(self):
        receipt = artifact.verify(self.file, self.digest, len(self.data))
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["observed_sha256"], self.digest)
        self.assertFalse(receipt["launch_performed"])
        self.assertFalse(receipt["runtime_compatible"])
        self.assertFalse(receipt["independent_source_authenticated"])

    def test_reject_corrupted_content_same_size(self):
        self.file.write_bytes(self.data[:-1] + b"\xff")
        with self.assertRaisesRegex(artifact.ArtifactError, "SHA-256 mismatch"):
            artifact.verify(self.file, self.digest, len(self.data))

    def test_reject_not_gguf_even_with_matching_hash(self):
        data = b"NOPE" + self.data[4:]
        self.file.write_bytes(data)
        with self.assertRaisesRegex(artifact.ArtifactError, "GGUF magic"):
            artifact.verify(self.file, hashlib.sha256(data).hexdigest(), len(data))

    def test_reject_truncation_and_expected_size_mismatch(self):
        with self.assertRaisesRegex(artifact.ArtifactError, "size mismatch"):
            artifact.verify(self.file, self.digest, len(self.data) + 1)

    def test_reject_symlink(self):
        shortcut = self.root / "shortcut.gguf"
        try:
            shortcut.symlink_to(self.file)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            self.skipTest("Windows user lacks symlink creation privileges")
        with self.assertRaises((OSError, artifact.ArtifactError)):
            artifact.verify(shortcut, self.digest, len(self.data))

    def test_reject_directory(self):
        with self.assertRaisesRegex(artifact.ArtifactError, "not a regular file"):
            artifact.verify(self.root, self.digest, len(self.data))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_reject_fifo(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises((OSError, artifact.ArtifactError)):
            artifact.verify(fifo, self.digest, len(self.data))

    @unittest.skipUnless(os.name == "nt", "Windows handle support required")
    def test_windows_rejects_nonlocal_paths_and_namespaces(self):
        for path in (r"\\example.invalid\share\model.gguf", r"\\.\NUL", str(self.file) + ":data"):
            with self.subTest(path=path), self.assertRaisesRegex(
                artifact.ArtifactError, "local drive path"
            ):
                artifact.verify(path, self.digest, len(self.data))

    @unittest.skipUnless(os.name == "nt", "Windows file sharing required")
    def test_windows_reads_unicode_path_and_rejects_existing_writer(self):
        sample = self.file.rename(self.root / "модель 模型.gguf")
        self.assertTrue(artifact.verify(sample, self.digest, len(self.data))["verified"])
        with sample.open("r+b"):
            with self.assertRaises(OSError) as raised:
                artifact.verify(sample, self.digest, len(self.data))
            self.assertEqual(raised.exception.winerror, 32)
        self.assertEqual(sample.read_bytes(), self.data)

    def test_reject_bad_digest_and_size_type(self):
        for bad in ("x"*64, "A"*64, "0"*63, "", None):
            with self.subTest(bad=bad), self.assertRaises(artifact.ArtifactError):
                artifact.verify(self.file, bad, len(self.data))
        for bad in (True, 1, -1, 1 << 80, "100"):
            with self.subTest(bad=bad), self.assertRaises(artifact.ArtifactError):
                artifact.verify(self.file, self.digest, bad)

    def test_cli_success_is_read_only(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = artifact.main(["--file", str(self.file), "--sha256", self.digest,
                                "--bytes", str(len(self.data))])
        self.assertEqual(rc, 0)
        result = json.loads(out.getvalue())
        self.assertFalse(result["launch_performed"])
        self.assertTrue(result["verified"])

    def test_cli_missing_file_fails_closed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = artifact.main(["--file", str(self.root / "missing.gguf"),
                                "--sha256", self.digest, "--bytes", str(len(self.data))])
        self.assertEqual(rc, 2)
        self.assertFalse(json.loads(err.getvalue())["verified"])


if __name__ == "__main__":
    unittest.main()
