"""Rejected journal identities must precede writable initialization policy."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


OWNER = "synthetic-preflight-owner"


def event(identifier, tick):
    return {
        "schema": EVENT_SCHEMA, "event_id": identifier, "agent_id": OWNER,
        "topic": "fixture", "observed_at_ns": tick, "recorded_at_ns": tick,
        "valid_from_ns": tick, "valid_to_ns": None, "visibility": "PRIVATE",
        "source": "synthetic-test", "source_revision": "1", "payload": {"tick": tick},
    }


class NoosphereOwnerPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "synthetic.sqlite3"
        with LocalNoosphere(self.path, OWNER) as journal:
            self.head = journal.append(event("first", 1), expected_head=journal.head())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        if os.name == "posix":
            self.path.chmod(0o640)

    def assert_rejected_without_initialization(self, owner):
        before = self.path.read_bytes()
        mode = self.path.stat().st_mode
        with self.assertRaisesRegex(NoosphereError, "schema or agent owner does not match"):
            LocalNoosphere(self.path, owner)
        self.assertTrue(self.path.read_bytes() == before, "rejected open changed the journal bytes")
        self.assertEqual(self.path.stat().st_mode, mode)
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_foreign_owner_rejected_before_wal_and_permission_changes(self):
        self.assert_rejected_without_initialization("different-owner")
        with LocalNoosphere(self.path, OWNER, read_only=True) as journal:
            self.assertEqual(journal.verify_chain(external_expected_head=self.head)["count"], 1)

    def test_corrupt_schema_rejected_before_wal_and_permission_changes(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE metadata SET v = ? WHERE k = 'schema'", ("invalid-fixture-schema",))
            db.commit()
        self.assert_rejected_without_initialization(OWNER)

    def test_mixed_case_metadata_table_still_preflights_existing_owner(self):
        # SQLite resolves these table names equally; sqlite_schema comparisons
        # must do so too before the persistent initialization policy runs.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("ALTER TABLE metadata RENAME TO interim_metadata")
            db.execute("ALTER TABLE interim_metadata RENAME TO Metadata")
        self.assert_rejected_without_initialization("different-owner")

    def test_metadata_view_preflights_owner_and_preserves_existing_compatibility(self):
        # Existing _setup accepts a metadata view through IF NOT EXISTS and
        # SELECT; preflight must inspect that same resolved relation.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("ALTER TABLE metadata RENAME TO metadata_records")
            db.execute("CREATE VIEW metadata AS SELECT k,v FROM metadata_records")
        self.assert_rejected_without_initialization("different-owner")
        with LocalNoosphere(self.path, OWNER) as journal:
            self.assertEqual(journal.head(), self.head)

    def test_valid_owner_can_initialize_wal_and_continue_existing_history(self):
        with LocalNoosphere(self.path, OWNER) as journal:
            self.assertEqual(journal.head(), self.head)
            self.assertEqual(journal._db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            head = journal.append(event("second", 2), expected_head=self.head)
            self.assertEqual(journal.verify_chain(external_expected_head=head)["count"], 2)
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_setup_rechecks_owner_if_it_changes_after_preflight(self):
        original_setup = LocalNoosphere._setup

        def change_owner_then_setup(journal):
            with closing(sqlite3.connect(self.path)) as peer:
                peer.execute("UPDATE metadata SET v = ? WHERE k = 'owner'", ("different-owner",))
                peer.commit()
            return original_setup(journal)

        with patch.object(LocalNoosphere, "_setup", change_owner_then_setup):
            with self.assertRaisesRegex(NoosphereError, "schema or agent owner does not match"):
                LocalNoosphere(self.path, OWNER)


if __name__ == "__main__":
    unittest.main()
