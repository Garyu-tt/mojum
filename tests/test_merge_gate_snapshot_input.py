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

from mojum.merge_gate import MAX_INPUT_BYTES, FetchError, SnapshotError, _load_snapshot, _run_gh_json, main


class SnapshotFileInputTests(unittest.TestCase):
    def test_merge_gate_file_starts_from_an_isolated_python(self):
        script = Path(__file__).resolve().parents[1] / "src" / "mojum" / "merge_gate.py"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-I", str(script), "--help"],
                cwd=cwd, text=True, capture_output=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--expected-head-sha", result.stdout)

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

    def test_live_gh_json_cannot_bypass_duplicate_key_guard(self):
        # --pr uses live gh output; it must share the offline decoder.
        duplicate = b'{"headRefOid":"a","headRefOid":"b"}'
        completed = subprocess.CompletedProcess(
            args=["gh", "pr", "view"], returncode=0,
            stdout=duplicate, stderr=b""
        )
        with mock.patch("mojum.merge_gate.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(SnapshotError, "duplicate JSON key"):
                _run_gh_json(["gh", "pr", "view", "123"], label="gh pr view")

    def test_live_gh_json_preserves_unicode_in_a_legacy_process_locale(self):
        expected = {"baseRefName": "release/\u00e9preuve", "name": "Contr\u00f4le"}
        wire = json.dumps(expected, ensure_ascii=False).encode("utf-8")
        emit = f"import sys; sys.stdout.buffer.write({wire!r})"
        script = (
            "import json, sys\n"
            "from mojum.merge_gate import _run_gh_json\n"
            f"print(json.dumps(_run_gh_json([sys.executable, '-c', {emit!r}], "
            "label='gh pr view')))\n"
        )
        source = Path(__file__).resolve().parents[1] / "src"
        env = dict(os.environ, LC_ALL="C", PYTHONCOERCECLOCALE="0",
                   PYTHONUTF8="0", PYTHONPATH=str(source))
        completed = subprocess.run([sys.executable, "-c", script], env=env,
                                   capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), expected)

    def test_duplicate_json_keys_cannot_mask_pr_or_check_state(self):
        # Last-wins JSON decoding can silently replace draft=True or a
        # failed check with an apparently safe value. Refuse the ambiguity.
        cases = (
            b'{"isDraft":true,"isDraft":false}',
            b'{"statusCheckRollup":[{"status":"FAILURE","status":"COMPLETED"}]}',
            b'{"statusCheckRollup":[{"name":"one","name":"two"}]}',
        )
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "snapshot.json"
                path.write_bytes(payload)
                with self.assertRaisesRegex(SnapshotError, "duplicate JSON key"):
                    _load_snapshot(str(path))
                with mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(payload))):
                    with self.assertRaisesRegex(SnapshotError, "duplicate JSON key"):
                        _load_snapshot("-")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main([
                        "--input", str(path),
                        "--expected-head-sha", "a" * 40,
                        "--expected-base-sha", "b" * 40,
                    ])
                self.assertEqual(code, 1)
                verdict = json.loads(output.getvalue())
                self.assertEqual(verdict["code"], "INVALID_INPUT")
                self.assertFalse(verdict["merge_performed"])

    def test_non_utf8_snapshot_fails_closed(self):
        payload = b'{"isDraft":\xff}'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid-encoding.json"
            path.write_bytes(payload)
            with self.assertRaisesRegex(SnapshotError, "valid UTF-8"):
                _load_snapshot(str(path))
            with mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(payload))):
                with self.assertRaisesRegex(SnapshotError, "valid UTF-8"):
                    _load_snapshot("-")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "--input", str(path),
                    "--expected-head-sha", "a" * 40,
                    "--expected-base-sha", "b" * 40,
                ])
            self.assertEqual(code, 1)
            verdict = json.loads(output.getvalue())
            self.assertEqual(verdict["code"], "INVALID_INPUT")
            self.assertFalse(verdict["merge_performed"])
            self.assertNotIn(str(path), output.getvalue())

    def test_non_utf8_live_gh_output_fails_closed(self):
        bad_wire = b'{"name":"\xff"}'
        emit = f"import sys; sys.stdout.buffer.write({bad_wire!r})"
        with self.assertRaisesRegex(FetchError, "undecodable UTF-8"):
            _run_gh_json([sys.executable, "-c", emit], label="gh pr view")

    def test_distinct_nested_snapshot_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            obj = {"statusCheckRollup": [{"name": "Aquarium", "status": "COMPLETED"}]}
            path.write_text(json.dumps(obj), encoding="utf-8")
            self.assertEqual(_load_snapshot(str(path)), obj)

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
