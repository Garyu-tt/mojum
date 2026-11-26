"""Canonical graph file admission must not hang runtime startup on a FIFO."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.graph import GraphError, load_graph


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BYTES = (ROOT / "src/mojum/data/first_loop.json").read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)


class GraphFileSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.graph = self.folder / "graph.json"
        self.graph.write_bytes(FIXTURE_BYTES)

    def assert_runtime_rejects(self, module, path):
        journal = self.folder / (module.rsplit(".", 1)[-1] + ".sqlite3")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-m", module, "--graph", str(path),
                 "--db", str(journal), "--ticks", "1"],
                cwd=self.folder, env=env, capture_output=True,
                encoding="utf-8", timeout=5,
            )
        except subprocess.TimeoutExpired:
            self.fail("runtime startup blocked on a special graph input")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("regular file", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(journal.exists())

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_runtime_clis_reject_writerless_fifo_before_journal_creation(self):
        fifo = self.folder / "graph.fifo"
        os.mkfifo(fifo)
        for module in ("mojum.aquarium", "mojum.portable_host"):
            with self.subTest(module=module):
                self.assert_runtime_rejects(module, fifo)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_runtime_rejects_symlink_to_writerless_fifo(self):
        fifo = self.folder / "graph.fifo"
        os.mkfifo(fifo)
        alias = self.folder / "graph-link.json"
        alias.symlink_to(fifo)
        self.assert_runtime_rejects("mojum.aquarium", alias)

    def test_regular_packaged_fixture_and_local_copy_are_unchanged(self):
        self.assertEqual(load_graph(ROOT / "src/mojum/data/first_loop.json"), FIXTURE)
        self.assertEqual(load_graph(self.graph), FIXTURE)

    def test_numeric_descriptor_is_rejected_without_consuming_or_closing_it(self):
        descriptor = os.open(self.graph, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self.addCleanup(self.close_if_open, descriptor)
        with self.assertRaises(GraphError):
            load_graph(descriptor)
        self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
        self.assertEqual(os.read(descriptor, len(FIXTURE_BYTES)), FIXTURE_BYTES)

    def test_symlink_to_regular_graph_is_still_supported(self):
        alias = self.folder / "graph-link.json"
        try:
            alias.symlink_to(self.graph)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(load_graph(alias), FIXTURE)

    def assert_descriptor_closed(self, descriptor):
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def close_if_open(self, descriptor):
        try:
            os.close(descriptor)
        except OSError:
            pass

    def test_rejects_actual_nonregular_descriptor_even_for_regular_path(self):
        descriptor = os.open(os.devnull, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self.addCleanup(self.close_if_open, descriptor)
        # Simulate a path replacement at open: admission must inspect the
        # returned descriptor, not the initially regular requested pathname.
        with patch("mojum.graph.os.open", return_value=descriptor):
            with self.assertRaisesRegex(GraphError, "regular file"):
                load_graph(self.graph)
        self.assert_descriptor_closed(descriptor)

    def test_descriptor_closes_when_inspection_fails(self):
        descriptor = os.open(self.graph, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self.addCleanup(self.close_if_open, descriptor)
        with patch("mojum.graph.os.open", return_value=descriptor), \
             patch("mojum.graph.os.fstat", side_effect=OSError("fixture inspection failure")):
            with self.assertRaisesRegex(GraphError, "Malformed graph JSON"):
                load_graph(self.graph)
        self.assert_descriptor_closed(descriptor)

    def test_descriptor_closes_after_failed_read(self):
        # A write-only regular fd makes the actual binary read fail. No bytes
        # are written; the caller still receives GraphError and the fd closes.
        descriptor = os.open(self.graph, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        self.addCleanup(self.close_if_open, descriptor)
        with patch("mojum.graph.os.open", return_value=descriptor):
            with self.assertRaisesRegex(GraphError, "Malformed graph JSON"):
                load_graph(self.graph)
        self.assert_descriptor_closed(descriptor)
        self.assertEqual(self.graph.read_bytes(), FIXTURE_BYTES)


if __name__ == "__main__":
    unittest.main()
