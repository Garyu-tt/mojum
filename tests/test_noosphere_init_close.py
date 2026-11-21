"""A failed LocalNoosphere constructor must not leak an open SQLite handle.

Explicitly keep references to the *actual* sqlite3.Connection objects created
by the constructor; do not rely on garbage collection or ResourceWarning timing.
All fixtures are disposable synthetic local databases, never user journals.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.noosphere import LocalNoosphere, NoosphereError


class FailedNoosphereInitClosesConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "synthetic.sqlite3"

    def _capture_failed_initialization(self, agent_id, error_type,
                                       message=None, *, setup_error=None):
        # Preserve the real function *before* replacing the module binding.
        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch("mojum.noosphere.sqlite3.connect", side_effect=tracked_connect):
            if setup_error is None:
                with self.assertRaises(error_type) as caught:
                    LocalNoosphere(self.db, agent_id)
            else:
                with patch.object(LocalNoosphere, "_setup", side_effect=setup_error):
                    with self.assertRaises(error_type) as caught:
                        LocalNoosphere(self.db, agent_id)
        if message is not None:
            self.assertIn(message, str(caught.exception))
        self.assertEqual(len(opened), 1, "constructor must create exactly one connection")

        # sqlite3.Connection.close() must have happened *synchronously*.
        # Retaining 'opened' forbids Python GC from concealing this failure.
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            opened[0].execute("SELECT 1")
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            opened[0].cursor()
        return opened[0]

    def test_wrong_owner_fails_closed_without_leaking_or_rewriting_original(self):
        with LocalNoosphere(self.db, "original-owner") as valid:
            original_head = valid.head()
            self._capture_failed_initialization(
                "different-owner", NoosphereError, "owner does not match"
            )
            # The original writer remains healthy after another owner was
            # rejected by the metadata check and its connection closed.
            self.assertEqual(valid.head(), original_head)
            self.assertEqual(valid.count(), 0)
        with LocalNoosphere(self.db, "original-owner") as reopened:
            self.assertEqual(reopened.head(), original_head)
            self.assertEqual(reopened.count(), 0)

    def test_malformed_persisted_store_schema_closes_failed_open(self):
        with LocalNoosphere(self.db, "original-owner"):
            pass
        # Disposable corruption fixture; never mutate any real journal.
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE metadata SET v = ? WHERE k = 'schema'",
                         ("invalid.synthetic.schema",))
            conn.commit()
        finally:
            conn.close()
        self._capture_failed_initialization(
            "original-owner", NoosphereError, "schema or agent owner"
        )

    def test_early_setup_exception_closes_connection_and_preserves_error(self):
        sentinel = RuntimeError("synthetic setup failure")
        self._capture_failed_initialization(
            "original-owner", RuntimeError, "synthetic setup failure",
            setup_error=sentinel,
        )

    def test_normal_open_close_remains_usable(self):
        with LocalNoosphere(self.db, "original-owner") as db:
            self.assertEqual(db.count(), 0)
            self.assertEqual(len(db.head()), 64)
            db.verify_chain()
        # Normal context manager must also be closed when it returns.
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            db._db.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
