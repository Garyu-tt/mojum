"""Bounded v1 graph ingestion must fail before parsing or creating a journal."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.graph import GraphError, MAX_GRAPH_BYTES, load_graph

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BYTES = (ROOT / "examples" / "first_loop.json").read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)


class GraphByteLimitTests(unittest.TestCase):
    def test_exact_256k_byte_boundary_accepts_valid_json(self):
        self.assertLess(len(FIXTURE_BYTES), MAX_GRAPH_BYTES)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "boundary.json"
            path.write_bytes(FIXTURE_BYTES + b" " * (MAX_GRAPH_BYTES - len(FIXTURE_BYTES)))
            self.assertEqual(path.stat().st_size, MAX_GRAPH_BYTES)
            self.assertEqual(load_graph(path), FIXTURE)

    def test_single_extra_byte_rejected_before_json_decoder(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "oversized.json"
            path.write_bytes(FIXTURE_BYTES + b" " * (MAX_GRAPH_BYTES + 1 - len(FIXTURE_BYTES)))
            with self.assertRaisesRegex(GraphError, "256 KiB byte limit"):
                load_graph(path)

    def test_multibyte_utf8_counts_wire_bytes_not_characters(self):
        document = json.loads(FIXTURE_BYTES)
        document["nodes"][0]["notes"]["purpose"] = "🐠" * 66000
        raw = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(raw), MAX_GRAPH_BYTES)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "multibyte.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(GraphError, "256 KiB byte limit"):
                load_graph(path)

    def test_invalid_utf8_is_typed_graph_error(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "invalid-utf8.json"
            path.write_bytes(FIXTURE_BYTES + b"\xff")
            with self.assertRaisesRegex(GraphError, "Malformed graph JSON"):
                load_graph(path)

    def test_existing_duplicate_key_gate_still_applies(self):
        raw = FIXTURE_BYTES.replace(b'"revision":', b'"revision":"collision", "revision":', 1)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "duplicate.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(GraphError, "Duplicate graph JSON key"):
                load_graph(path)

    def test_aquarium_cli_does_not_create_journal_for_oversized_graph(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            path = root / "untrusted.json"
            journal = root / "should-not-exist.sqlite3"
            path.write_bytes(FIXTURE_BYTES + b" " * (MAX_GRAPH_BYTES + 1 - len(FIXTURE_BYTES)))
            result = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium",
                 "--graph", str(path), "--db", str(journal), "--ticks", "1"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("256 KiB byte limit", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
