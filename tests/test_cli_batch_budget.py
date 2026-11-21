"""CLI batch budget regression: failed continuation preserves durable history.

Synthetic CPU-only fixture. No production data or hardware.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from mojum.aquarium import Aquarium, main


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CliBatchBudgetTests(unittest.TestCase):
    def test_rejected_whole_batch_preserves_committed_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "journal.sqlite3"
            argv = ["mojum-aquarium", "--db", str(db), "--ticks", "2"]
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
                with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                    main()
                with Aquarium(db, SEED, GRAPH) as reader:
                    before = reader.checkpoint()
                    self.assertEqual(before["tick"], 2)
                    self.assertEqual(reader.store.count(), 3)

                # One more frame would exceed the three-event cap.
                argv[-1] = "1"
                error_output = io.StringIO()
                with patch.object(sys, "argv", argv), redirect_stderr(error_output):
                    with self.assertRaises(SystemExit) as rejected:
                        main()
                self.assertEqual(rejected.exception.code, 2)
                self.assertIn("requested ticks exceed remaining journal event budget",
                              error_output.getvalue())

                with Aquarium(db, SEED, GRAPH) as resumed:
                    self.assertEqual(resumed.checkpoint(), before)
                    self.assertEqual(resumed.store.count(), 3)
                    self.assertEqual(resumed.store.verify_chain()["count"], 3)


if __name__ == "__main__":
    unittest.main()
