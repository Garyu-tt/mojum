"""Canonical graph admission must precede sensory-curiosity journal access."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.graph import MAX_GRAPH_BYTES


ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))
WIRE = json.dumps(GRAPH, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class SensoryGraphInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    def run_cli(self, db, *, graph=None, ticks=0):
        command = [sys.executable, "-B", "-m", "mojum.sensory_curiosity_lab",
                   "--db", str(db), "--ticks", str(ticks)]
        if graph is not None:
            command.extend(("--graph", str(graph)))
        return subprocess.run(command, cwd=self.root, env=self.env,
                              capture_output=True, timeout=20)

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_rejected_without_journal_changes(self, db, graph):
        before = self.snapshot()
        result = self.run_cli(db, graph=graph, ticks=1)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"invalid graph input", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_keys_are_rejected_before_new_journal(self):
        cases = {
            "root": b'{"id":"hidden-graph",' + WIRE[1:],
            "nested": WIRE.replace(b'"bpm":60', b'"bpm":120,"bpm":60', 1),
        }
        for name, wire in cases.items():
            with self.subTest(name=name):
                graph = self.root / (name + ".json")
                graph.write_bytes(wire)
                self.assert_rejected_without_journal_changes(
                    self.root / (name + ".sqlite3"), graph,
                )

    def test_graph_over_byte_limit_is_rejected_before_new_journal(self):
        graph = self.root / "oversized.json"
        graph.write_bytes(WIRE + b" " * (MAX_GRAPH_BYTES + 1 - len(WIRE)))
        self.assertEqual(graph.stat().st_size, MAX_GRAPH_BYTES + 1)
        self.assert_rejected_without_journal_changes(self.root / "new.sqlite3", graph)

    def test_malformed_missing_and_semantically_invalid_graphs_report_usage_error(self):
        invalid_schema = {**GRAPH, "schema": "unsupported-graph/0"}
        cases = {
            "missing": None,
            "utf8": b"\xff",
            "truncated": b'{"schema":',
            "schema": json.dumps(invalid_schema).encode("utf-8"),
        }
        for name, wire in cases.items():
            with self.subTest(name=name):
                graph = self.root / (name + ".json")
                if wire is not None:
                    graph.write_bytes(wire)
                self.assert_rejected_without_journal_changes(
                    self.root / (name + ".sqlite3"), graph,
                )

    def test_rejected_graphs_preserve_existing_journal_and_resume_anchor(self):
        db = self.root / "existing.sqlite3"
        first = self.run_cli(db, ticks=2)
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = json.loads(first.stdout)
        cases = {
            "duplicate": b'{"id":"hidden-graph",' + WIRE[1:],
            "oversized": WIRE + b" " * (MAX_GRAPH_BYTES + 1 - len(WIRE)),
        }
        for name, wire in cases.items():
            with self.subTest(name=name):
                graph = self.root / (name + ".json")
                graph.write_bytes(wire)
                self.assert_rejected_without_journal_changes(db, graph)
        reopened = self.run_cli(db)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout), expected)

    def test_packaged_and_exact_byte_limit_custom_graphs_cold_resume(self):
        custom = {**GRAPH, "id": "sensory-custom-\u661f"}
        wire = json.dumps(custom, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        graph = self.root / "custom.json"
        graph.write_bytes(wire + b" " * (MAX_GRAPH_BYTES - len(wire)))
        self.assertEqual(graph.stat().st_size, MAX_GRAPH_BYTES)
        for name, source in (("packaged", None), ("custom", graph)):
            with self.subTest(name=name):
                db = self.root / (name + ".sqlite3")
                first = self.run_cli(db, graph=source, ticks=2)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(json.loads(first.stdout)["checkpoint"]["tick"], 2)
                resumed = self.run_cli(db, graph=source, ticks=1)
                control = self.run_cli(self.root / (name + "-control.sqlite3"),
                                       graph=source, ticks=3)
                self.assertEqual((resumed.returncode, control.returncode), (0, 0),
                                 (resumed.stderr, control.stderr))
                self.assertEqual(json.loads(resumed.stdout), json.loads(control.stdout))
                self.assertEqual(json.loads(resumed.stdout)["private_events"], 4)


if __name__ == "__main__":
    unittest.main()
