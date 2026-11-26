"""Real PRIVATE trace exports must report success on legacy stdout encodings."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "src/mojum/data/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "src/mojum/data/first_loop.json").read_text(encoding="utf-8"))
OWNER = "Данте-星-\U0001f680"


class TraceExportCliStdoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "synthetic.sqlite3"
        self.genome = self.root / "genome.json"
        seed = dict(SEED, genome_id=OWNER)
        self.genome.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        with Aquarium(self.db, seed, GRAPH, agent_id=OWNER,
                      neural_mode="disabled") as aquarium:
            self.frames = [aquarium.step(dx=0.025) for _ in range(4)]
            self.head = aquarium.store.head()
            self.count = aquarium.store.count()
        self.source_bytes = self.db.read_bytes()

    def run_export(self, output, encoding, *options, head=None):
        command = [
            sys.executable, "-m", "mojum.aquarium_trace_export",
            "--db", str(self.db), "--genome", str(self.genome),
            "--output", str(output), "--neural-mode", "disabled",
            "--allow-private-export", "--expected-head",
            self.head if head is None else head, *options,
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                   PYTHONIOENCODING=f"{encoding}:strict")
        return subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                              timeout=30, check=False)

    def assert_export(self, result, output, start, stop):
        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii"))
        self.assertEqual(result.stderr, b"")
        # The receipt is one portable JSON record; decoding retains every
        # character in the Unicode owner, including a supplementary code point.
        self.assertEqual(len(result.stdout.splitlines()), 1)
        receipt = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(receipt["agent_id"], OWNER)
        self.assertEqual(receipt["journal_head"], self.head)
        self.assertEqual(receipt["event_count"], self.count)
        self.assertEqual(receipt["frames"], stop - start)
        self.assertEqual(receipt["first_tick"], start)
        self.assertEqual(receipt["next_tick"], stop)
        self.assertEqual(receipt["has_more"], stop < len(self.frames))
        self.assertEqual(receipt["source_total_ticks"], len(self.frames))
        self.assertNotIn("interoception", receipt)
        self.assertNotIn("movement_request", receipt)

        payload = output.read_bytes()
        # Only stdout is ASCII-escaped. The trace remains UTF-8 with its
        # original canonical frame bytes and independently checked digests.
        self.assertIn(OWNER, payload.decode("utf-8"))
        lines = payload.splitlines(keepends=True)
        self.assertTrue(all(line.endswith(b"\n") for line in lines))
        self.assertEqual([json.loads(line) for line in lines[:-1]], self.frames[start:stop])
        footer = json.loads(lines[-1])
        self.assertEqual(footer["agent_id"], OWNER)
        self.assertEqual(footer["journal_head"], self.head)
        self.assertEqual(footer["stored_events"], self.count)
        self.assertEqual(footer["trace_range"], {
            "start_tick": start, "next_tick": stop,
            "has_more": stop < len(self.frames), "source_total_ticks": len(self.frames),
        })
        frames_wire = b"".join(lines[:-1])
        self.assertEqual(footer["trace_integrity"], {
            "schema": "mojum.aquarium.trace-frames-sha256/1",
            "sha256": hashlib.sha256(frames_wire).hexdigest(),
            "byte_length": len(frames_wire), "frame_count": stop - start,
        })
        self.assertEqual(receipt["trace_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.db.read_bytes(), self.source_bytes)
        self.assertEqual(list(self.root.glob(".mojum-private-trace-*")), [])

    def test_full_and_segment_exports_return_unicode_owner_on_legacy_stdout(self):
        for encoding in ("ascii", "cp1251"):
            for start, stop, options in (
                    (0, 4, ()), (1, 3, ("--start-tick", "1", "--frame-limit", "2"))):
                with self.subTest(encoding=encoding, start=start):
                    output = self.root / f"{encoding}-{start}.jsonl"
                    result = self.run_export(output, encoding, *options)
                    self.assert_export(result, output, start, stop)

    def test_retry_preserves_published_trace_and_does_not_emit_success_receipt(self):
        output = self.root / "trace.jsonl"
        first = self.run_export(output, "ascii")
        self.assert_export(first, output, 0, 4)
        published = output.read_bytes()
        repeated = self.run_export(output, "ascii")
        self.assertEqual(repeated.returncode, 2, repeated.stderr)
        self.assertEqual(repeated.stdout, b"")
        self.assertIn(b"new filename", repeated.stderr)
        self.assertEqual(output.read_bytes(), published)
        self.assertEqual(self.db.read_bytes(), self.source_bytes)
        self.assertEqual(list(self.root.glob(".mojum-private-trace-*")), [])

    def test_wrong_anchor_does_not_publish_trace_or_success_receipt(self):
        output = self.root / "trace.jsonl"
        wrong_head = "0" * 64
        self.assertNotEqual(wrong_head, self.head)
        result = self.run_export(output, "ascii", head=wrong_head)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(output.exists())
        self.assertEqual(self.db.read_bytes(), self.source_bytes)
        self.assertEqual(list(self.root.glob(".mojum-private-trace-*")), [])


if __name__ == "__main__":
    unittest.main()
