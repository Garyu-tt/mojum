"""Regression coverage for race-safe cold reads of PRIVATE school fixtures.

Tests deliberately replace a validated path at the next os.open call. They do
not access any external data, network endpoint, or live school artifacts.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.school import SchoolContractError
from mojum.school_verify import _read_regular_bounded


class SchoolVerifierDescriptorTests(unittest.TestCase):
    def test_regular_file_budget_and_preexisting_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            path.write_bytes(b"hello")
            self.assertEqual(_read_regular_bounded(path, 5), b"hello")
            with self.assertRaisesRegex(SchoolContractError, "byte budget"):
                _read_regular_bounded(path, 4)
            alias = Path(temp) / "alias"
            try:
                alias.symlink_to(path)
            except (OSError, NotImplementedError):
                return  # Platform may prohibit symlinks without special privileges.
            with self.assertRaisesRegex(SchoolContractError, "nonregular"):
                _read_regular_bounded(alias, 5)

    @unittest.skipUnless(os.name == "posix", "requires stable POSIX inode identity")
    def test_regular_file_replacement_is_not_accepted_even_with_equal_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            replacement = Path(temp) / "replacement"
            path.write_bytes(b"same bytes")
            replacement.write_bytes(b"same bytes")
            original_open = os.open
            opened = []

            def replace_during_open(candidate, flags, *args, **kwargs):
                if Path(candidate) == path:
                    replacement.replace(path)
                fd = original_open(candidate, flags, *args, **kwargs)
                opened.append(fd)
                return fd

            with patch("mojum.school_verify.os.open", side_effect=replace_during_open):
                with self.assertRaisesRegex(SchoolContractError, "changed while opening"):
                    _read_regular_bounded(path, 100)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0])  # Rejected descriptor must be closed.

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"),
                         "requires POSIX O_NOFOLLOW")
    def test_symlink_swap_before_open_cannot_read_another_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            outside = Path(temp) / "outside.private"
            path.write_bytes(b"expected")
            outside.write_bytes(b"secret that must not be read")
            original_open = os.open

            def swap_for_symlink(candidate, flags, *args, **kwargs):
                if Path(candidate) == path:
                    path.unlink()
                    path.symlink_to(outside)
                return original_open(candidate, flags, *args, **kwargs)

            with patch("mojum.school_verify.os.open", side_effect=swap_for_symlink):
                with self.assertRaises(SchoolContractError):
                    _read_regular_bounded(path, 100)
            self.assertTrue(path.is_symlink())
            self.assertEqual(outside.read_bytes(), b"secret that must not be read")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NONBLOCK"),
                         "requires POSIX nonblocking FIFO open")
    def test_fifo_swap_fails_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            path.write_bytes(b"expected")
            original_open = os.open
            opened = []

            def swap_for_fifo(candidate, flags, *args, **kwargs):
                if Path(candidate) == path:
                    path.unlink()
                    os.mkfifo(path)
                    self.assertTrue(flags & os.O_NONBLOCK)
                fd = original_open(candidate, flags, *args, **kwargs)
                opened.append(fd)
                return fd

            with patch("mojum.school_verify.os.open", side_effect=swap_for_fifo):
                with self.assertRaisesRegex(SchoolContractError, "changed while opening"):
                    _read_regular_bounded(path, 100)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0])


if __name__ == "__main__":
    unittest.main()
