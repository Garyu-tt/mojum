"""Standalone room-check CLI must reject inputs before an unbounded wait."""

from contextlib import redirect_stdout
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum import room_spec


ROOM = b'{"width":4,"height":4,"obstacles":[],"start":[1.0,2.0]}'
ROOT = Path(__file__).resolve().parents[1]


class RoomCheckInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_cli(self, path, output_format="json"):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "mojum.room_spec", str(path),
             "--format", output_format],
            env=env, cwd=self.root, capture_output=True, timeout=5,
        )

    def assert_rejected(self, path, reason):
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], room_spec.REPORT_SCHEMA)
        self.assertIs(report["valid"], False)
        self.assertIsNone(report["summary"])
        self.assertIn(reason, report["errors"][0])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"),
                         "requires POSIX named pipes")
    def test_writerless_fifo_is_rejected_without_waiting_for_a_writer(self):
        path = self.root / "room.fifo"
        os.mkfifo(path)
        self.assert_rejected(path, "regular file")
        result = self.run_cli(path, "text")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(b"INVALID: 1 error(s)", result.stdout)
        self.assertIn(b"regular file", result.stdout)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"),
                         "requires POSIX symlinks and named pipes")
    def test_symlink_to_fifo_is_rejected_without_waiting(self):
        pipe = self.root / "room.fifo"
        os.mkfifo(pipe)
        link = self.root / "room.json"
        link.symlink_to(pipe)
        self.assert_rejected(link, "regular file")

    @unittest.skipUnless(os.name == "posix", "requires POSIX character device")
    def test_character_device_is_rejected_as_nonregular(self):
        self.assert_rejected(Path(os.devnull), "regular file")

    def test_valid_file_and_exact_byte_limit_preserve_number_tokens(self):
        for size in (len(ROOM), room_spec.MAX_INPUT_BYTES):
            with self.subTest(size=size):
                path = self.root / "room.json"
                data = ROOM + b" " * (size - len(ROOM))
                path.write_bytes(data)
                result = self.run_cli(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertIs(report["valid"], True)
                self.assertEqual(report["summary"]["start"], ["1.0", "2.0"])
                self.assertEqual(path.read_bytes(), data)

    def test_limit_plus_one_is_rejected(self):
        path = self.root / "room.json"
        path.write_bytes(ROOM + b" " * (room_spec.MAX_INPUT_BYTES + 1 - len(ROOM)))
        self.assert_rejected(path, "byte limit")

    def test_directory_and_missing_path_keep_structured_failure(self):
        self.assert_rejected(self.root, "room document")
        self.assert_rejected(self.root / "missing.json", "cannot read room document")

    def test_regular_file_symlink_stays_compatible(self):
        target = self.root / "target.json"
        target.write_bytes(ROOM)
        link = self.root / "room.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        result = self.run_cli(link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)["valid"], True)

    @unittest.skipUnless(os.name == "posix", "requires nonblocking POSIX open")
    def test_opened_descriptor_is_checked_even_if_path_was_regular(self):
        path = self.root / "room.json"
        path.write_bytes(ROOM)
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        output = io.StringIO()
        try:
            with patch.object(room_spec.os, "open", return_value=read_fd), \
                    redirect_stdout(output):
                result = room_spec.main([str(path), "--format", "json"])
            self.assertEqual(result, 1)
            self.assertIn("regular file", json.loads(output.getvalue())["errors"][0])
            with self.assertRaises(OSError):
                os.fstat(read_fd)
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

    def test_descriptor_is_closed_if_inspection_fails(self):
        path = self.root / "room.json"
        path.write_bytes(ROOM)
        real_open = os.open
        opened = []

        def record_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        output = io.StringIO()
        with patch.object(room_spec.os, "open", side_effect=record_open), \
                patch.object(room_spec.os, "fstat", side_effect=OSError(errno.EIO, "I/O failure")), \
                redirect_stdout(output):
            result = room_spec.main([str(path), "--format", "json"])
        self.assertEqual(result, 1)
        self.assertIn("cannot read room document", json.loads(output.getvalue())["errors"][0])
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_file_growth_after_inspection_cannot_exceed_the_read_budget(self):
        path = self.root / "room.json"
        path.write_bytes(ROOM)
        real_fstat = os.fstat

        def grow_after_inspection(fd):
            info = real_fstat(fd)
            with path.open("ab") as stream:
                stream.write(b" " * (room_spec.MAX_INPUT_BYTES + 1))
            return info

        output = io.StringIO()
        with patch.object(room_spec.os, "fstat", side_effect=grow_after_inspection), \
                redirect_stdout(output):
            result = room_spec.main([str(path), "--format", "json"])
        self.assertEqual(result, 1)
        self.assertIn("byte limit", json.loads(output.getvalue())["errors"][0])

    @unittest.skipUnless(os.name == "posix", "requires POSIX open contract")
    def test_unavailable_nonblocking_open_fails_before_opening_input(self):
        output = io.StringIO()
        with patch.object(room_spec.os, "O_NONBLOCK", 0), \
                patch.object(room_spec.os, "open") as opener, \
                redirect_stdout(output):
            result = room_spec.main([str(self.root / "room.json"), "--format", "json"])
        self.assertEqual(result, 1)
        self.assertIn("O_NONBLOCK", json.loads(output.getvalue())["errors"][0])
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
