"""CLI budget preflight regression on disposable synthetic SQLite only."""

import contextlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import main


class AquariumCliBudgetTests(unittest.TestCase):
    def test_exact_boundary_then_rejected_batch_preserves_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "synthetic.sqlite3"

            def invoke(ticks):
                argv = ["mojum.aquarium", "--db", str(db), "--ticks", str(ticks)]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    main()

            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
                invoke(2)  # one bootstrap + two frames fills budget
                with contextlib.closing(sqlite3.connect(db)) as conn:
                    before = conn.execute("SELECT seq, event_id, event_hash FROM events ORDER BY seq").fetchall()
                self.assertEqual(len(before), 3)
                with self.assertRaises(SystemExit) as error:
                    invoke(1)
                self.assertEqual(error.exception.code, 2)
                with contextlib.closing(sqlite3.connect(db)) as conn:
                    after = conn.execute("SELECT seq, event_id, event_hash FROM events ORDER BY seq").fetchall()
                self.assertEqual(after, before)

    def test_oversized_first_batch_does_not_append_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "synthetic.sqlite3"
            argv = ["mojum.aquarium", "--db", str(db), "--ticks", "3"]
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3), patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
            self.assertEqual(error.exception.code, 2)
            # The constructor may persist the initial bootstrap, but never a partial frame.
            with contextlib.closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
