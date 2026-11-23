"""Offline regression tests for metadata-only SQLite schema audit."""
from contextlib import closing
import gc
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import warnings

from mojum.sqlite_schema_fingerprint import SchemaAuditError, fingerprint


class FingerprintTests(unittest.TestCase):
    def _connect(self, path):
        return closing(sqlite3.connect(path))

    def test_deterministic_and_data_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            with self._connect(path) as db:
                with db:
                    db.execute("CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT)")
                    db.execute("INSERT INTO secrets(value) VALUES ('DO_NOT_LEAK')")
            first = fingerprint(path)
            with self._connect(path) as db:
                with db:
                    db.execute("INSERT INTO secrets(value) VALUES ('ANOTHER_SECRET')")
            second = fingerprint(path)
            self.assertEqual(first, second)
            self.assertEqual(first["entries"], 1)
            self.assertNotIn("secrets", str(first))
            self.assertNotIn("DO_NOT_LEAK", str(first))
            with self._connect(path) as db:
                with db:
                    db.execute("CREATE INDEX secret_idx ON secrets(value)")
            self.assertNotEqual(first["sha256"], fingerprint(path)["sha256"])

    def test_repeated_calls_do_not_emit_resource_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "warning.sqlite3"
            with self._connect(path) as db:
                with db:
                    db.execute("CREATE TABLE t(x)")
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                for _ in range(5):
                    fingerprint(path)
                    gc.collect()

    def test_reject_non_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "not-sqlite"
            path.write_text("hello world")
            with self.assertRaises(SchemaAuditError):
                fingerprint(path)

    def test_reject_empty_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.sqlite3"
            with self._connect(path):
                pass
            with self.assertRaises(SchemaAuditError):
                fingerprint(path)

    def test_reject_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "db"
            with self._connect(path) as db:
                with db:
                    db.execute("CREATE TABLE t(x)")
            link = Path(temp) / "link"
            link.symlink_to(path)
            with self.assertRaises(SchemaAuditError):
                fingerprint(link)

    def test_missing_file_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing"
            with self.assertRaises(SchemaAuditError):
                fingerprint(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
