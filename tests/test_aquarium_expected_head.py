"""Independent journal-head anchor: fail closed without creating a new journal.

An external head only makes sense for history that already exists. A mistyped
or malformed anchor must not leave behind a fresh PRIVATE SQLite journal, and a
wrong anchor must not append or mutate the existing one.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, main
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class AquariumExpectedHeadApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "anchor.sqlite3"

    def open(self, **kwargs):
        return Aquarium(self.db, SEED, GRAPH, agent_id=SEED["genome_id"], **kwargs)

    def test_malformed_head_fails_before_creating_a_journal(self):
        for head in ("", "0" * 63, "0" * 65, "A" * 64, "g" * 64, 0, b"0" * 64, None.__class__, ["0" * 64]):
            with self.subTest(head=repr(head)):
                with self.assertRaisesRegex(AquariumError, "expected head"):
                    self.open(external_expected_head=head)
                self.assertFalse(self.db.exists())

    def test_anchor_on_missing_or_empty_journal_never_bootstraps(self):
        head = "0" * 64
        with self.assertRaisesRegex(AquariumError, "existing journal"):
            self.open(external_expected_head=head)
        self.assertFalse(self.db.exists())

    def test_correct_anchor_reopens_and_appends(self):
        with self.open() as writer:
            writer.step()
            anchor = writer.checkpoint()["journal_head"]
            expected_count = writer.store.count()
        with self.open(external_expected_head=anchor) as resumed:
            checkpoint = resumed.checkpoint()
            self.assertEqual(checkpoint["journal_head"], anchor)
            self.assertEqual(checkpoint["tick"], 1)
            resumed.step()
            self.assertEqual(resumed.store.count(), expected_count + 1)

    def test_wrong_anchor_leaves_existing_journal_unchanged(self):
        with self.open() as writer:
            writer.step()
            checkpoint = writer.checkpoint()
        with self.assertRaises(NoosphereError):
            self.open(external_expected_head="1" * 64)
        with self.open(external_expected_head=checkpoint["journal_head"]) as reader:
            self.assertEqual(reader.checkpoint(), checkpoint)


class AquariumExpectedHeadCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "anchor.sqlite3"

    def run_cli(self, ticks, *options):
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
             "--ticks", str(ticks), *options],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, timeout=60,
        )

    def test_malformed_anchor_fails_before_bootstrap(self):
        for head in ("", "0" * 63, "Z" * 64, "0" * 65):
            with self.subTest(head=head):
                result = self.run_cli(1, "--expected-head", head)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("expected-head", result.stderr)
                self.assertFalse(self.db.exists())

    def test_anchor_without_existing_journal_fails_closed(self):
        result = self.run_cli(1, "--expected-head", "0" * 64)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("existing journal", result.stderr)
        self.assertFalse(self.db.exists())

    def test_correct_anchor_appends_and_wrong_anchor_preserves_journal(self):
        first = self.run_cli(2)
        self.assertEqual(first.returncode, 0, first.stderr)
        checkpoint = json.loads(first.stdout)
        head = checkpoint["journal_head"]
        self.assertEqual((checkpoint["tick"], checkpoint["stored_events"]), (2, 3))

        wrong = self.run_cli(1, "--expected-head", "1" * 64)
        self.assertEqual(wrong.returncode, 2, wrong.stderr)
        unchanged = self.run_cli(0, "--expected-head", head)
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertEqual(json.loads(unchanged.stdout)["journal_head"], head)

        resumed = self.run_cli(1, "--expected-head", head)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        report = json.loads(resumed.stdout)
        self.assertEqual((report["tick"], report["stored_events"]), (3, 4))
        self.assertNotEqual(report["journal_head"], head)


if __name__ == "__main__":
    unittest.main()
