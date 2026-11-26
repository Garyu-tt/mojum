"""Failed dispatcher initialization must release its actual SQLite handle."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import spartan_coordinator as coordinator


class ConnectCleanupTests(unittest.TestCase):
    def setUp(self):
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

    def test_malformed_database_releases_file_without_garbage_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.sqlite3"
            content = b"not a SQLite database" * 100
            path.write_bytes(content)
            if os.name == "posix":
                path.chmod(0o600)
            try:
                with patch.object(coordinator.sqlite3, "connect", self.capture):
                    with self.assertRaisesRegex(sqlite3.DatabaseError, "not a database"):
                        coordinator.connect(str(path))
                self.assertEqual(len(self.opened), 1)
                self.assert_closed(self.opened[0])
                self.assertEqual(path.read_bytes(), content)
                # Keeping the connection referenced rules out reliance on GC.
                path.unlink()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                self.close_captured()

    def test_each_startup_statement_closes_before_reraising_same_failure(self):
        statements = (
            "PRAGMA busy_timeout=30000",
            "PRAGMA journal_mode=WAL",
            "PRAGMA foreign_keys=ON",
        )
        for statement in statements:
            for error_type in (sqlite3.OperationalError, KeyboardInterrupt):
                with self.subTest(statement=statement, error=error_type.__name__):
                    failure = error_type("startup refused")

                    class FailingConnection(sqlite3.Connection):
                        def execute(self, sql, *args, **kwargs):
                            if sql == statement:
                                raise failure
                            return super().execute(sql, *args, **kwargs)

                    db = self.real_connect(":memory:", factory=FailingConnection)
                    self.opened.append(db)
                    with patch.object(coordinator.sqlite3, "connect", return_value=db):
                        with self.assertRaises(error_type) as raised:
                            coordinator.connect(":memory:")
                    self.assertIs(raised.exception, failure)
                    self.assert_closed(db)

    def test_success_returns_open_configured_connection_to_caller(self):
        with patch.object(coordinator.sqlite3, "connect", self.capture):
            db = coordinator.connect(":memory:")
        self.assertIs(db, self.opened[0])
        self.assertIs(db.row_factory, sqlite3.Row)
        self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        coordinator.create_schema(db)
        self.assertEqual(coordinator.init(db, 2, 100), {"slots": 2})
        self.assertEqual(coordinator.status(db)["slots"], 2)


if __name__ == "__main__":
    unittest.main()
