"""Actual-byte input budget regression for the optional Projects v2 board reader.

A falsified pre-read stat result simulates growth between a metadata check
and the open/read. No GitHub account or network connection is required.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mojum.project_v2_board import BoardError, MAX_BYTES, _load_json


class SnapshotByteReadTests(unittest.TestCase):
    @staticmethod
    def _boundary_json() -> bytes:
        prefix = b'{"payload":"'
        suffix = b'"}'
        data = prefix + b"x" * (MAX_BYTES - len(prefix) - len(suffix)) + suffix
        assert len(data) == MAX_BYTES
        return data

    def test_exact_byte_boundary_remains_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boundary.json"
            path.write_bytes(self._boundary_json())
            document = _load_json(path)
            self.assertEqual(len(document["payload"]), MAX_BYTES - len(b'{"payload":""}'))

    def test_stat_size_cannot_bypass_actual_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.json"
            # Appended JSON whitespace is valid, so the old unbounded reader
            # would silently accept it if stat() reported a stale small size.
            path.write_bytes(self._boundary_json() + b" ")
            fake_metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=1)
            with patch.object(Path, "stat", return_value=fake_metadata):
                with self.assertRaisesRegex(BoardError, "oversized input JSON"):
                    _load_json(path)

    def test_invalid_utf8_is_a_typed_board_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-utf8.json"
            path.write_bytes(b'{"invalid":"\xff"}')
            with self.assertRaisesRegex(BoardError, "invalid input JSON"):
                _load_json(path)

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"),
        "POSIX FIFO safety regression requires nonblocking open",
    )
    def test_fifo_input_is_rejected_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostile-fifo.json"
            os.mkfifo(path, mode=0o600)
            with self.assertRaisesRegex(BoardError, "missing or oversized input JSON"):
                _load_json(path)

    def test_directory_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BoardError):
                _load_json(Path(directory))

    def test_unavailable_file_is_a_typed_board_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            with self.assertRaisesRegex(BoardError, "invalid input JSON"):
                _load_json(path)


if __name__ == "__main__":
    unittest.main()
