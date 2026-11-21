"""Real journal segment export: no truncation, no hidden skipped history.

Runs against actual SQLite/Noosphere/Aquarium events. Both the full history
and any selected frames are replay-verified before PRIVATE JSONL publication.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace
from mojum.noosphere import LocalNoosphere


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "src/mojum/data/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "src/mojum/data/first_loop.json").read_text(encoding="utf-8"))


class TraceSegmentExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "one-creature.sqlite3"
        self.output = self.root / "segment.jsonl"
        with Aquarium(self.db, SEED, GRAPH) as a:
            self.actual = [a.step() for _ in range(17)]
            self.head = a.store.head()
            self.total_events = a.store.count()
        self.original_source_bytes = self.db.read_bytes()

    def export(self, *, output=None, **options):
        return export_private_trace(
            self.db, output or self.output, SEED, GRAPH,
            allow_private_export=True,
            expected_journal_head=self.head, **options,
        )

    def trace(self, path=None):
        rows = [json.loads(x) for x in (path or self.output).read_text().splitlines()]
        return rows[:-1], rows[-1]

    def test_selected_middle_segment_exactly_matches_canonical_payload(self):
        with patch.object(LocalNoosphere, "dataset",
                          side_effect=AssertionError("full dataset load")):
            meta = self.export(start_tick=5, frame_limit=4)
        selected, footer = self.trace()
        self.assertEqual(selected, self.actual[5:9])
        self.assertEqual([frame["tick"] for frame in selected], [5, 6, 7, 8])
        self.assertEqual(meta["frames"], 4)
        self.assertEqual(meta["first_tick"], 5)
        self.assertEqual(meta["next_tick"], 9)
        self.assertTrue(meta["has_more"])
        self.assertEqual(meta["source_total_ticks"], 17)
        self.assertEqual(meta["event_count"], self.total_events)
        self.assertEqual(meta["journal_head"], self.head)
        self.assertEqual(footer["trace_range"], {
            "start_tick": 5, "next_tick": 9,
            "has_more": True, "source_total_ticks": 17,
        })
        self.assertEqual(footer["stored_events"], 18)
        self.assertEqual(self.db.read_bytes(), self.original_source_bytes)

    def test_last_partial_segment_is_explicitly_complete(self):
        meta = self.export(start_tick=14, frame_limit=5)
        selected, footer = self.trace()
        self.assertEqual(selected, self.actual[14:])
        self.assertEqual(meta["frames"], 3)
        self.assertEqual(meta["next_tick"], 17)
        self.assertFalse(meta["has_more"])
        self.assertFalse(footer["trace_range"]["has_more"])

    def test_full_history_default_not_silently_trimmed(self):
        meta = self.export()
        selected, footer = self.trace()
        self.assertEqual(selected, self.actual)
        self.assertEqual(meta["frames"], 17)
        self.assertEqual(meta["first_tick"], 0)
        self.assertEqual(meta["next_tick"], 17)
        self.assertFalse(meta["has_more"])
        self.assertEqual(footer["journal_head"], self.head)

    def test_rejects_unpaired_or_malformed_slice_before_disk_output(self):
        invalid = (
            {"start_tick": 0},
            {"frame_limit": 5},
            {"start_tick": -1, "frame_limit": 3},
            {"start_tick": True, "frame_limit": 3},
            {"start_tick": 0, "frame_limit": False},
            {"start_tick": 0, "frame_limit": 0},
            {"start_tick": 0, "frame_limit": 5001},
            {"start_tick": 10000, "frame_limit": 1},
        )
        for kw in invalid:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    self.export(**kw)
                self.assertFalse(self.output.exists())
        self.assertEqual(self.db.read_bytes(), self.original_source_bytes)

    def test_empty_future_segment_refused_without_mutating_history(self):
        with self.assertRaisesRegex(ValueError, "no committed frames"):
            self.export(start_tick=17, frame_limit=1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.read_bytes(), self.original_source_bytes)

    def test_independent_segments_can_form_consecutive_viewer_trace(self):
        parts = []
        for index, number in ((0, 6), (6, 6), (12, 5)):
            file = self.root / f"segment-{index:04d}.jsonl"
            result = self.export(output=file, start_tick=index, frame_limit=number)
            frames, footer = self.trace(file)
            self.assertEqual([f["tick"] for f in frames],
                             list(range(index, index + number)))
            self.assertEqual(result["journal_head"], footer["journal_head"])
            self.assertEqual(result["source_total_ticks"], 17)
            parts.extend(frames)
        self.assertEqual(parts, self.actual)
        self.assertEqual([f["tick"] for f in parts], list(range(17)))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.read_bytes(), self.original_source_bytes)

    def test_cli_segment_arguments_and_private_metadata_only(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [
            sys.executable, "-m", "mojum.aquarium_trace_export",
            "--db", str(self.db), "--output", str(self.output),
            "--allow-private-export", "--expected-head", self.head,
            "--start-tick", "10", "--frame-limit", "4",
        ]
        run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                             text=True, check=False, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        meta = json.loads(run.stdout)
        self.assertEqual(meta["first_tick"], 10)
        self.assertEqual(meta["next_tick"], 14)
        self.assertTrue(meta["has_more"])
        self.assertNotIn("interoception", run.stdout)
        self.assertNotIn("movement_request", run.stdout)
        self.assertEqual(self.trace()[0], self.actual[10:14])


if __name__ == "__main__":
    unittest.main()
