"""Graph CLI input admission must not wait indefinitely for a FIFO writer."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.graph import GraphError
from mojum.graph_audit_cli import _read_json


ROOT = Path(__file__).resolve().parents[1]


class GraphAuditDescriptorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "graph.json"
        self.source.write_bytes((ROOT / "examples" / "first_loop.json").read_bytes())
        self.target = self.root / "output.json"

    def cli(self, *arguments):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src") + (
            os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH") else ""
        )
        # Keep a hang regression in a subprocess: kill it after the deadline
        # instead of wedging the entire repository's test runner.
        return subprocess.run(
            [sys.executable, "-m", "mojum.graph_audit_cli", *arguments],
            cwd=ROOT, env=environment, text=True, capture_output=True,
            check=False, timeout=5,
        )

    def assert_rejected_without_output(self, result):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("graph audit rejected", result.stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob(".mojum-graph-v2-*")), [])

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "requires nonblocking FIFO support")
    def test_fifo_graph_and_labels_fail_without_blocking_or_publishing(self):
        fifo = self.root / "input.fifo"
        os.mkfifo(fifo)
        cases = (
            ("audit", "--input", str(fifo)),
            ("upgrade", "--input", str(fifo), "--output", str(self.target)),
            ("downgrade", "--input", str(fifo), "--output", str(self.target)),
            ("upgrade", "--input", str(self.source), "--labels", str(fifo),
             "--output", str(self.target)),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assert_rejected_without_output(result)
                self.assertIn("must be a regular file", result.stderr)

    def test_directory_graph_and_labels_fail_without_publishing(self):
        for arguments in (
            ("upgrade", "--input", str(self.root), "--output", str(self.target)),
            ("upgrade", "--input", str(self.source), "--labels", str(self.root),
             "--output", str(self.target)),
        ):
            with self.subTest(arguments=arguments):
                self.assert_rejected_without_output(self.cli(*arguments))

    def test_regular_file_symlink_remains_accepted(self):
        alias = self.root / "alias.json"
        try:
            alias.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        result = self.cli("audit", "--input", str(alias))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["node_count"], 6)

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "requires nonblocking FIFO support")
    def test_fifo_symlink_fails_without_blocking(self):
        fifo = self.root / "input.fifo"
        alias = self.root / "alias.json"
        os.mkfifo(fifo)
        alias.symlink_to(fifo)
        result = self.cli("audit", "--input", str(alias))
        self.assert_rejected_without_output(result)
        self.assertIn("must be a regular file", result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX character device")
    def test_character_device_is_rejected_before_read_and_descriptor_is_closed(self):
        original_open = os.open
        descriptors = []

        def record_open(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch("mojum.graph_audit_cli.os.open", side_effect=record_open):
            with self.assertRaisesRegex(GraphError, "must be a regular file"):
                _read_json(Path(os.devnull), 100)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_failed_stream_wrapper_closes_opened_descriptor(self):
        original_open = os.open
        descriptors = []

        def record_open(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch("mojum.graph_audit_cli.os.open", side_effect=record_open), \
             patch("mojum.graph_audit_cli.os.fdopen", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                _read_json(self.source, 100)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    @unittest.skipUnless(os.name == "posix", "POSIX nonblocking admission")
    def test_missing_nonblock_fails_before_open(self):
        with patch("mojum.graph_audit_cli.os.O_NONBLOCK", 0), \
             patch("mojum.graph_audit_cli.os.open") as mocked_open:
            with self.assertRaisesRegex(GraphError, "without O_NONBLOCK"):
                _read_json(self.source, 100)
            mocked_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
