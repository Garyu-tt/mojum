"""Twin CLI receipts must survive narrow stdout encodings after committing."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.twin_lab import RECEIPT_SCHEMA, create_pair, mentor_steps


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "mojum" / "data"
SEED = json.loads((DATA / "genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((DATA / "first_loop.json").read_text(encoding="utf-8"))
ENCODINGS = ("ascii:strict", "cp1251:strict")
LEFT_ID = "twin.left.Ж🙂"
RIGHT_ID = "twin.right.漢字"


class TwinLabStdoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def cli(self, encoding, *arguments):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONIOENCODING"] = encoding
        return subprocess.run(
            [sys.executable, "-m", "mojum.twin_lab", *map(str, arguments)],
            cwd=self.directory, env=env, capture_output=True, timeout=30,
        )

    def receipt(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        # ASCII JSON is lossless under both encodings, including non-BMP IDs.
        report = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(report["schema"], RECEIPT_SCHEMA)
        return report

    def snapshot(self, db, agent_id):
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = agent_id
        with Aquarium(db, seed, GRAPH, agent_id=agent_id) as reopened:
            return {
                "summary": {
                    "agent_id": reopened.agent_id,
                    "tick": reopened.tick,
                    "position_m": [reopened.world.x, reopened.world.y],
                    "stored_events": reopened.store.count(),
                },
                "head": reopened.store.head(),
            }

    def test_unicode_pair_receipt_and_no_clobber_retry(self):
        for encoding in ENCODINGS:
            with self.subTest(encoding=encoding):
                prefix = encoding.split(":")[0]
                left = self.directory / (prefix + "-left.sqlite3")
                right = self.directory / (prefix + "-right.sqlite3")
                arguments = (
                    "pair", "--left-db", left, "--right-db", right,
                    "--left-id", LEFT_ID, "--right-id", RIGHT_ID,
                )
                report = self.receipt(self.cli(encoding, *arguments))
                before = [self.snapshot(left, LEFT_ID),
                          self.snapshot(right, RIGHT_ID)]
                self.assertEqual(report["twins"],
                                 [state["summary"] for state in before])
                self.assertEqual([twin["agent_id"] for twin in report["twins"]],
                                 [LEFT_ID, RIGHT_ID])
                self.assertEqual([twin["tick"] for twin in report["twins"]],
                                 [0, 0])
                self.assertEqual([twin["stored_events"] for twin in report["twins"]],
                                 [1, 1])
                before_bytes = [left.read_bytes(), right.read_bytes()]

                retry = self.cli(encoding, *arguments)
                self.assertEqual(retry.returncode, 2, retry.stderr)
                self.assertEqual(retry.stdout, b"")
                self.assertIn(b"refusing an existing or symlinked journal", retry.stderr)
                self.assertNotIn(b"Traceback", retry.stderr)
                self.assertEqual([left.read_bytes(), right.read_bytes()], before_bytes)
                self.assertEqual([self.snapshot(left, LEFT_ID),
                                  self.snapshot(right, RIGHT_ID)], before)

    def test_unicode_mentor_receipts_resume_exactly_one_twin(self):
        for encoding in ENCODINGS:
            with self.subTest(encoding=encoding):
                prefix = encoding.split(":")[0]
                left = self.directory / (prefix + "-left.sqlite3")
                right = self.directory / (prefix + "-right.sqlite3")
                pair = create_pair(left, right, LEFT_ID, RIGHT_ID, SEED, GRAPH)
                initial_left = self.snapshot(left, LEFT_ID)
                sibling = self.snapshot(right, RIGHT_ID)
                sibling_bytes = right.read_bytes()
                previous = initial_left
                for ticks, total in ((2, 2), (1, 3)):
                    result = self.cli(
                        encoding, "mentor", "--db", left, "--agent-id", LEFT_ID,
                        "--ticks", ticks, "--dx", 0.05, "--dy", 0,
                    )
                    report = self.receipt(result)
                    reopened = self.snapshot(left, LEFT_ID)
                    self.assertEqual(report["twin"], reopened["summary"])
                    self.assertEqual(report["twin"]["agent_id"], LEFT_ID)
                    self.assertEqual(report["twin"]["tick"], total)
                    self.assertEqual(report["twin"]["stored_events"], total + 1)
                    self.assertEqual(report["shared_heritable_sha256"],
                                     pair["shared_heritable_sha256"])
                    self.assertNotEqual(reopened["head"], previous["head"])
                    self.assertEqual(right.read_bytes(), sibling_bytes)
                    self.assertEqual(self.snapshot(right, RIGHT_ID), sibling)
                    previous = reopened

                # A separate zero-step process confirms cold resume is read-only.
                zero = self.receipt(self.cli(
                    encoding, "mentor", "--db", left, "--agent-id", LEFT_ID,
                    "--ticks", 0,
                ))
                self.assertEqual(zero, report)
                self.assertEqual(self.snapshot(left, LEFT_ID), previous)

    def test_ascii_receipt_bytes_keep_existing_format(self):
        left = self.directory / "left.sqlite3"
        right = self.directory / "right.sqlite3"
        reference_left = self.directory / "reference-left.sqlite3"
        reference_right = self.directory / "reference-right.sqlite3"
        expected_pair = create_pair(reference_left, reference_right,
                                    "twin-a", "twin-b", SEED, GRAPH)
        pair = self.cli(
            "ascii:strict", "pair", "--left-db", left, "--right-db", right,
            "--left-id", "twin-a", "--right-id", "twin-b",
        )
        self.assertEqual(self.receipt(pair), expected_pair)
        expected_mentor = mentor_steps(reference_left, "twin-a", SEED, GRAPH,
                                       ticks=2, dx=0.05, dy=0)
        mentor = self.cli(
            "ascii:strict", "mentor", "--db", left, "--agent-id", "twin-a",
            "--ticks", 2, "--dx", 0.05, "--dy", 0,
        )
        self.assertEqual(self.receipt(mentor), expected_mentor)
        for result, expected in ((pair, expected_pair), (mentor, expected_mentor)):
            old_wire = (json.dumps(expected, indent=2, ensure_ascii=False,
                                   allow_nan=False) + "\n").encode("ascii")
            self.assertEqual(result.stdout.replace(b"\r\n", b"\n"), old_wire)


if __name__ == "__main__":
    unittest.main()
