"""Fail-closed local file input for exact-head QA preflight (issue #415)."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from mojum.merge_gate import MAX_INPUT_BYTES, SnapshotError, _load_snapshot, main


class SnapshotFileInputTests(unittest.TestCase):
    def test_regular_json_is_read_and_path_read_text_is_not_used(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pr.json"
            path.write_text('{"headRefOid":"abc"}', encoding="utf-8")
            # No unchecked Path.read_text() after a separately sampled stat().
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("unbounded path read")):
                self.assertEqual(_load_snapshot(str(path)), {"headRefOid": "abc"})

    def test_overdeep_bounded_json_is_rejected_without_traceback(self):
        # A 30 KiB valid JSON array still exceeds the Python parser depth.
        payload = b"[" * 15000 + b"]" * 15000
        self.assertLess(len(payload), MAX_INPUT_BYTES)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "deep.json"
            path.write_bytes(payload)
            with self.assertRaisesRegex(SnapshotError, "nesting limit"):
                _load_snapshot(str(path))
            with mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(payload))):
                with self.assertRaisesRegex(SnapshotError, "nesting limit"):
                    _load_snapshot("-")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "--input", str(path),
                    "--expected-head-sha", "a" * 40,
                    "--expected-base-sha", "b" * 40,
                ])
            self.assertEqual(exit_code, 1)
            verdict = json.loads(output.getvalue())
            self.assertEqual(verdict["code"], "INVALID_INPUT")
            self.assertFalse(verdict["merge_performed"])
            self.assertNotIn(str(path), output.getvalue())

    def test_missing_secure_open_flags_fail_closed_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pr.json"
            path.write_text('{"safe":true}', encoding="utf-8")
            for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY"):
                with self.subTest(flag=flag), mock.patch.object(
                    os, flag, 0, create=True,
                ):
                    with self.assertRaisesRegex(
                        SnapshotError, "secure snapshot file reads unavailable"
                    ):
                        _load_snapshot(str(path))

    def test_large_file_is_rejected_before_json_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "oversized.json"
            with path.open("wb") as output:
                output.write(b" " * (MAX_INPUT_BYTES + 1))
            with self.assertRaisesRegex(SnapshotError, "snapshot exceeds"):
                _load_snapshot(str(path))

    @unittest.skipUnless(os.name == "posix" and getattr(os, "O_NOFOLLOW", 0),
                         "POSIX final-symlink rejection required")
    def test_preexisting_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "pr.json"
            path.write_text('{"safe":true}', encoding="utf-8")
            alias = root / "alias.json"
            alias.symlink_to(path)
            with self.assertRaisesRegex(SnapshotError, "source unavailable"):
                _load_snapshot(str(alias))

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "POSIX nonblocking FIFO support required")
    def test_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / "pr.fifo"
            os.mkfifo(fifo)
            root = Path(__file__).resolve().parents[1]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(root / "src")
            run = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from mojum.merge_gate import _load_snapshot; _load_snapshot(sys.argv[1])",
                 str(fifo)],
                cwd=root, env=env, text=True, capture_output=True,
                check=False, timeout=5,
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("snapshot source must be a regular file", run.stderr)

    @unittest.skipUnless(
        os.name == "posix" and
        all(getattr(os, f, 0) for f in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")),
        "POSIX pinned parent directory support required",
    )
    def test_symlinked_snapshot_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            source = actual / "snapshot.json"
            source.write_text('{"safe":true}', encoding="utf-8")
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(SnapshotError, "source unavailable"):
                _load_snapshot(str(alias / "snapshot.json"))
            self.assertEqual(_load_snapshot(str(source)), {"safe": True})

    @unittest.skipUnless(os.name == "posix", "POSIX secure file mode")
    def test_parent_traversal_and_unavailable_nofollow_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            source = actual / "snapshot.json"
            source.write_text('{"safe":true}', encoding="utf-8")
            with self.assertRaisesRegex(SnapshotError, "parent traversal"):
                _load_snapshot(str(actual / ".." / "actual" / "snapshot.json"))
            with mock.patch("mojum.merge_gate.os.O_NOFOLLOW", 0):
                with self.assertRaisesRegex(SnapshotError, "reads unavailable"):
                    _load_snapshot(str(source))

    def test_error_does_not_expose_sensitive_input_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = "PRIVATE-access-token-NOT-FOR-LOGS.json"
            path = str(Path(temp) / secret)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--input", path, "--expected-head-sha", "a" * 40,
                             "--expected-base-sha", "b" * 40])
            self.assertEqual(code, 1)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["code"], "INVALID_INPUT")
            self.assertNotIn(secret, stdout.getvalue())
            self.assertEqual(result["error"], "snapshot source unavailable")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and Path("/proc/self/fd").is_dir(),
        "Linux process fd inventory required",
    )
    def test_all_descriptors_are_closed_on_invalid_json(self):
        # Patching os.open now masks the real function in os.supports_dir_fd,
        # making the secure-open preflight fail before JSON is ever parsed.
        # Compare live file descriptors instead, including opened ancestors.
        def live_fds():
            result = set()
            for item in os.listdir("/proc/self/fd"):
                try:
                    fd = int(item)
                    os.fstat(fd)
                except (ValueError, OSError):
                    continue  # os.listdir's own transient directory handle
                result.add(fd)
            return result

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.json"
            path.write_bytes(b"{bad}")
            before = live_fds()
            with self.assertRaises(json.JSONDecodeError):
                _load_snapshot(str(path))
            self.assertEqual(live_fds(), before)


if __name__ == "__main__":
    unittest.main()
