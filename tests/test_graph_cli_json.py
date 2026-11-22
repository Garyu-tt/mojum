"""File-based graph entry point contracts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GraphCliJsonTests(unittest.TestCase):
    def test_both_clis_reject_duplicate_members_before_execution(self):
        fixture = (ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8")
        wire = json.dumps(json.loads(fixture))
        cases = {
            "schema": wire.replace('"schema":', '"schema": "mechanicus.universal-graph/2", "schema":', 1),
            "bpm": wire.replace('"bpm":', '"bpm": 120, "bpm":', 1),
        }
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            graph = root / "duplicate.json"
            journal = root / "aquarium.sqlite"
            commands = (
                ("mojum", str(graph)),
                ("mojum.aquarium", "--graph", str(graph), "--db", str(journal)),
            )
            for key, ambiguous_json in cases.items():
                self.assertNotEqual(wire, ambiguous_json)
                graph.write_text(ambiguous_json, encoding="utf-8")
                for command in commands:
                    with self.subTest(key=key, module=command[0]):
                        run = subprocess.run(
                            [sys.executable, "-m", *command, "--ticks", "1"],
                            cwd=ROOT, capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(run.returncode, 2, run.stderr)
                        self.assertIn(f"Duplicate graph JSON key: {key!r}", run.stderr)
                        self.assertEqual(run.stdout, "")
                        self.assertFalse(journal.exists())

    def test_fixture_cli_runs_graph_without_episode_sink(self):
        fixture = ROOT / "examples" / "first_loop.json"
        graph = json.loads(fixture.read_text(encoding="utf-8"))
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "episode"]
        graph["edges"] = [edge for edge in graph["edges"] if edge["to"]["node"] != "episode"]
        with tempfile.TemporaryDirectory() as dirname:
            graph_path = Path(dirname) / "no-episode.json"
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "mojum", str(graph_path), "--ticks", "2"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["tick"], 2)
        self.assertIsNone(report["last_episode"])
        self.assertEqual(report["last_frame"]["room"]["world"]["observed_at_ns"], 10_000_000)
