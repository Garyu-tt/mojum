"""Actual SQLite handle ownership at capacity-probe startup boundaries."""
from contextlib import closing, redirect_stderr
from io import StringIO
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_capacity_probe as probe


class CapacityConnectCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "probe.sqlite3"
        self.real_connect = sqlite3.connect
        self.opened = []
        self.addCleanup(self.close_captured)

    def close_captured(self):
        for db in self.opened:
            db.close()

    def capture(self, *args, **kwargs):
        db = self.real_connect(*args, **kwargs)
        self.opened.append(db)
        return db

    def assert_closed(self, db):
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            db.cursor()

    def malformed_file(self):
        content = b"not a SQLite database" * 100
        self.path.write_bytes(content)
        if os.name == "posix":
            self.path.chmod(0o600)
        return content

    def test_malformed_database_closes_before_returning_error(self):
        content = self.malformed_file()
        with patch.object(probe.sqlite3, "connect", self.capture):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "not a database"):
                probe.connect(str(self.path))
        self.assertEqual(len(self.opened), 1)
        self.assert_closed(self.opened[0])
        self.assertEqual(self.path.read_bytes(), content)
        self.path.unlink()  # Must work on Windows without waiting for GC.
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_cli_failure_also_releases_malformed_database(self):
        self.malformed_file()
        stderr = StringIO()
        with patch.object(probe.sqlite3, "connect", self.capture), redirect_stderr(stderr):
            self.assertEqual(probe.main(["--db", str(self.path), "report"]), 1)
        self.assertIn("file is not a database", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assert_closed(self.opened[0])
        self.path.unlink()

    def test_each_startup_failure_and_interrupt_closes_same_connection(self):
        for statement in ("PRAGMA busy_timeout=30000", "PRAGMA journal_mode=WAL",
                          "PRAGMA synchronous=FULL"):
            for error_type in (sqlite3.OperationalError, KeyboardInterrupt):
                with self.subTest(statement=statement, error=error_type.__name__):
                    failure = error_type("startup refused")

                    class FailingConnection(sqlite3.Connection):
                        def execute(self, sql, *args, **kwargs):
                            if sql == statement:
                                raise failure
                            return super().execute(sql, *args, **kwargs)

                    def connect_failing(*args, **kwargs):
                        kwargs["factory"] = FailingConnection
                        return self.capture(*args, **kwargs)

                    with patch.object(probe.sqlite3, "connect", connect_failing):
                        with self.assertRaises(error_type) as raised:
                            probe.connect(str(self.path))
                    self.assertIs(raised.exception, failure)
                    self.assert_closed(self.opened[-1])

    def test_success_retains_durability_and_persistent_telemetry(self):
        with patch.object(probe, "_boot_id", return_value="synthetic-test-boot"):
            with closing(probe.connect(str(self.path))) as db:
                self.assertIs(db.row_factory, sqlite3.Row)
                self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
                probe.initialize(db, "run")
                probe.begin(db, "op", "worker", "model", now_ns=100)
                probe.finish(db, "op", "success", output_tokens=7, now_ns=200)
                before = probe.report(db)
            with closing(probe.connect(str(self.path))) as db:
                self.assertEqual(probe.report(db), before)
                self.assertEqual(before["completed_operations"], 1)
                self.assertEqual(before["output_tokens_reported"], 7)
                self.assertEqual(before["peak_simultaneous_successful_workers"], 1)


if __name__ == "__main__":
    unittest.main()
