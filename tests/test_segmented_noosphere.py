"""R0.16 atomic segmented SQLite, immutable boundary, CAS and privacy QA.

All files are disposable local fixtures; never open, repair, rewrite or
delete a user's old PRIVATE Noosphere journal.
"""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError
from mojum.segmented_noosphere import (
    SegmentedNoosphere, STORE_SCHEMA, SEGMENT_SCHEMA,
    MAX_BATCH, MAX_JOURNAL_EVENTS,
)


OWNER = "synthetic-journal-fixture-001"
ZERO = "0" * 64


def record(n, *, visibility="PRIVATE", id_override=None):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": id_override if id_override is not None else "synthetic-%09d" % n,
        "agent_id": OWNER,
        "topic": "fixture.synthetic",
        "observed_at_ns": n * 1000,
        "recorded_at_ns": n * 1000,
        "valid_from_ns": n * 1000,
        "valid_to_ns": None,
        "visibility": visibility,
        "source": "mojum.segmented.fixture.no-real-life",
        "source_revision": "test-seed-1",
        "payload": {"sequence": n, "classification": "PRIVATE_SYNTHETIC"},
    }


class AtomicSegmentQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "synthetic-segments.sqlite3"

    def open(self, segment_size=64):
        return SegmentedNoosphere(self.db, OWNER, segment_size=segment_size)

    def fill(self, store, start, end, *, batch_size=71):
        tip = store.head()
        for begin in range(start, end, batch_size):
            tip = store.append_many(
                [record(j) for j in range(begin, min(end, begin + batch_size))],
                expected_head=tip,
            )
        return tip

    def test_new_private_database_is_0600_before_sqlite_opens_it(self):
        if os.name != "posix":
            self.skipTest("POSIX file mode regression")
        original_connect = sqlite3.connect
        seen = []

        def check_mode_before_connect(database, *args, **kwargs):
            if str(database) == str(self.db):
                self.assertTrue(self.db.is_file(), "secure file must preexist connect")
                self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
                seen.append(True)
            return original_connect(database, *args, **kwargs)

        with patch("mojum.segmented_noosphere.sqlite3.connect",
                   side_effect=check_mode_before_connect):
            with self.open() as store:
                self.assertEqual(store.head(), ZERO)
        self.assertEqual(seen, [True])

    def test_world_readable_existing_journal_is_refused_without_rewrite(self):
        if os.name != "posix":
            self.skipTest("POSIX permission regression")
        with self.open() as source:
            head = source.append(record(0), expected_head=ZERO)
        self.db.chmod(0o644)
        try:
            before = self.db.read_bytes()
            with self.assertRaisesRegex(NoosphereError, "owner-only"):
                self.open()
            self.assertEqual(self.db.read_bytes(), before)
        finally:
            self.db.chmod(0o600)
        with self.open() as source:
            self.assertEqual(source.verify_chain()["head"], head)

    def test_195_real_events_split_four_immutable_linked_boundaries(self):
        with self.open() as q:
            final = self.fill(q, 0, 195)
            self.assertEqual(q.count(), 195)
            self.assertEqual(q.head(), final)
            report = q.verify_chain(external_expected_head=final)
            self.assertEqual((report["count"], report["segments"]), (195, 4))
            self.assertEqual(report["schema"], STORE_SCHEMA)
            b = list(q._db.execute(
                "SELECT segment_index,first_seq,prior_segment_head,schema "
                "FROM segments ORDER BY segment_index"
            ))
            self.assertEqual([r["first_seq"] for r in b], [1, 65, 129, 193])
            self.assertTrue(all(r["schema"] == SEGMENT_SCHEMA for r in b))
            self.assertEqual(b[0]["prior_segment_head"], ZERO)
            self.assertEqual(b[1]["prior_segment_head"],
                             q.event_at(64)["event_hash"])
            self.assertEqual(b[2]["prior_segment_head"],
                             q.event_at(128)["event_hash"])
            self.assertEqual(b[3]["prior_segment_head"],
                             q.event_at(192)["event_hash"])
            receipts = list(
                q.iter_segment_receipts(external_expected_head=final)
            )
            self.assertEqual(len(receipts), 4)
            self.assertEqual([x["events"] for x in receipts],
                             [64, 64, 64, 3])
            self.assertEqual(receipts[0]["prior_segment_head"], ZERO)
            self.assertEqual(receipts[-1]["segment_head"], final)
            self.assertEqual(receipts[-1]["last_seq"], 195)
            self.assertTrue(all("payload" not in x for x in receipts))
            self.assertEqual(
                [x["seq"] for x in q.iter_events(
                    include_nonpublic=True, batch_size=17, after_seq=190,
                )], [191, 192, 193, 194, 195],
            )
            self.assertEqual(
                q.dataset(limit=300)["projection"]["items"], [],
                "default projection must not expose PRIVATE events",
            )
            private = q.dataset(include_nonpublic=True, limit=3)
            self.assertEqual(len(private["projection"]["items"]), 3)
            self.assertEqual(private["projection"]["items"][0]["seq"], 1)
            self.assertNotEqual(private["sha256"], ZERO)
            with self.assertRaises(NoosphereError):
                q.verify_chain(external_expected_head="f" * 64)
        with self.open() as replay:
            self.assertEqual(replay.head(), final)
            self.assertEqual(
                replay.verify_chain(external_expected_head=final)["segments"], 4,
            )

    def test_batch_rollback_crosses_segment_boundary_with_duplicate(self):
        with self.open() as q:
            self.fill(q, 0, 63)
            old_head = q.head()
            # Would create a new boundary at seq 65. Later duplicate is
            # rejected, so BOTH the event and boundary must roll back.
            with self.assertRaises(NoosphereError):
                q.append_many([
                    record(63), record(64),
                    record(65, id_override="synthetic-000000001"),
                ], expected_head=old_head)
            self.assertEqual(q.count(), 63)
            self.assertEqual(q.head(), old_head)
            self.assertEqual(q.verify_chain()["segments"], 1)
            q.append_many([record(63), record(64)], expected_head=old_head)
            self.assertEqual(q.verify_chain()["segments"], 2)

    def test_cas_stale_writer_cannot_fork_at_boundary(self):
        with self.open() as a, self.open() as b:
            self.fill(a, 0, 64)
            old_head = b.head()
            a.append(record(64), expected_head=old_head)
            new_head = a.head()
            self.assertNotEqual(new_head, old_head)
            with self.assertRaisesRegex(NoosphereError, "CAS failed"):
                b.append(record(65), expected_head=old_head)
            self.assertEqual(a.head(), new_head)
            self.assertEqual(a.count(), 65)
            self.assertEqual(a.verify_chain()["segments"], 2)
        with self.open() as q:
            self.assertEqual(q.count(), 65)
            self.assertEqual(q.head(), new_head)

    def test_commit_then_caller_crash_reopen_without_duplicate(self):
        with self.open() as q:
            self.fill(q, 0, 64)
            expected = q.head()
            real = q.append

            def write_then_crash(event, *, expected_head):
                real(event, expected_head=expected_head)
                raise RuntimeError("simulated after-commit process interruption")

            with patch.object(q, "append", side_effect=write_then_crash):
                with self.assertRaisesRegex(RuntimeError, "after-commit"):
                    q.append(record(64), expected_head=expected)
            self.assertEqual(q.count(), 65)
            with self.assertRaisesRegex(NoosphereError, "CAS failed"):
                q.append(record(64), expected_head=expected)
        with self.open() as after:
            self.assertEqual(after.count(), 65)
            self.assertEqual(after.verify_chain()["segments"], 2)
            after.append(record(65), expected_head=after.head())
            self.assertEqual(after.count(), 66)

    def test_tampered_segment_link_and_internal_event_are_detected(self):
        with self.open() as q:
            head = self.fill(q, 0, 129)
            with closing(sqlite3.connect(self.db)) as db, db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "DELETE FROM segments WHERE segment_index = 1"
                    )
                db.execute("DROP TRIGGER no_boundary_update")
                db.execute(
                    "UPDATE segments SET prior_segment_head = ? "
                    "WHERE segment_index = 1", ("a"*64,),
                )
            with self.assertRaisesRegex(NoosphereError, "mislinked segment"):
                q.verify_chain(external_expected_head=head)
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute(
                    "UPDATE segments SET prior_segment_head = ? "
                    "WHERE segment_index = 1",
                    (q.event_at(64)["event_hash"],),
                )
                db.execute("DROP TRIGGER no_segment_update")
                db.execute(
                    "UPDATE segmented_events SET wire_json = ? WHERE seq=129",
                    (json.dumps(record(128, visibility="PUBLIC"),
                                sort_keys=True, separators=(",", ":")),),
                )
            with self.assertRaises(NoosphereError):
                q.verify_chain(external_expected_head=head)

    def test_old_local_noosphere_and_segmented_db_refuse_migration(self):
        with self.open() as q:
            q.append(record(0), expected_head=ZERO)
            original = q.verify_chain()
        with self.assertRaises(NoosphereError):
            with LocalNoosphere(self.db, OWNER):
                pass
        with self.open() as q:
            self.assertEqual(q.verify_chain(), original)
        legacy_db = self.db.with_name("original-legacy.sqlite3")
        with LocalNoosphere(legacy_db, OWNER) as legacy:
            head = legacy.append(record(0), expected_head=ZERO)
            saved = legacy.verify_chain()
            self.assertEqual(head, saved["head"])
        with self.assertRaises(NoosphereError):
            with SegmentedNoosphere(legacy_db, OWNER, segment_size=64):
                pass
        with LocalNoosphere(legacy_db, OWNER) as legacy:
            self.assertEqual(legacy.verify_chain(), saved)

    def test_existing_journal_missing_core_table_refuses_reopen(self):
        """A damaged existing store must not be silently rebuilt as empty."""
        with self.open() as journal:
            original_head = journal.append(record(0), expected_head=ZERO)
            self.assertEqual(journal.verify_chain()["head"], original_head)

        # Simulate physical schema loss on a disposable PRIVATE fixture.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DROP TABLE segmented_events")

        with self.assertRaisesRegex(NoosphereError, "missing core tables"):
            self.open()

        # The failed open must not recreate the vanished events table.
        with closing(sqlite3.connect(self.db)) as db:
            tables = {
                name for (name,) in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertNotIn("segmented_events", tables)
        self.assertIn("segments", tables)

    def test_limits_and_invalid_batch_leave_no_partial_rows(self):
        with self.assertRaises(NoosphereError):
            SegmentedNoosphere(self.db, OWNER, segment_size=True)
        self.assertFalse(self.db.exists())
        with self.open() as q:
            for batch in ([], tuple(), [record(0)] * (MAX_BATCH + 1)):
                with self.subTest(length=len(batch)):
                    with self.assertRaises(NoosphereError):
                        q.append_many(batch, expected_head=ZERO)
            self.assertEqual(q.count(), 0)
            q.append(record(0), expected_head=ZERO)
            for wrong in (-1, 1.5, "bad", True):
                with self.subTest(expected=wrong):
                    with self.assertRaises(NoosphereError):
                        q.append(record(1), expected_head=wrong)
            self.assertEqual(q.count(), 1)


    def test_readonly_schema_probe_closes_actual_sqlite_connection_on_all_paths(self):
        """No deferred GC: real handles are closed on success and rejection."""
        with self.open() as source:
            original_head = source.append(record(0), expected_head=ZERO)
            self.assertEqual(source.verify_chain()["head"], original_head)

        # Keep strong refs so GC cannot hide a genuine unclosed connection.
        real_connect = sqlite3.connect
        connections = []

        class ObservedConnection(sqlite3.Connection):
            close_calls = 0

            def close(self):
                self.close_calls += 1
                return super().close()

        def observed_connect(*args, **kwargs):
            if "factory" in kwargs:
                raise AssertionError("Unexpected SQLite connection factory")
            connection = real_connect(
                *args, factory=ObservedConnection, **kwargs
            )
            connections.append(connection)
            return connection

        # Separate preexisting SQLite has no segmented metadata at all:
        # one rejected SQL query (inside closing), not a writable migration.
        empty_db = self.db.with_name("synthetic-not-a-segmented-source.sqlite3")
        with closing(real_connect(empty_db)) as empty:
            empty.execute("CREATE TABLE irrelevant_fixture(value INTEGER)")

        with patch("mojum.segmented_noosphere.sqlite3.connect",
                   side_effect=observed_connect):
            # Successful existing-db reopen makes two connections: readonly
            # manifest probe and normal writer. Both must close deterministically.
            with self.open() as source:
                self.assertEqual(source.verify_chain()["head"], original_head)
                self.assertEqual(source.count(), 1)

            # Post-query schema/owner rejection, after probe has already closed.
            for owner, size in (
                (OWNER, 128),
                ("different-synthetic-owner", 64),
            ):
                with self.subTest(owner=owner, segment_size=size):
                    with self.assertRaisesRegex(NoosphereError, "wrong owner"):
                        SegmentedNoosphere(
                            self.db, owner, segment_size=size,
                        )

            # Error while reading schema must close the probe immediately.
            with self.assertRaisesRegex(NoosphereError, "readable segmented manifest"):
                SegmentedNoosphere(empty_db, OWNER, segment_size=64)

        self.assertEqual(len(connections), 5, "one open plus four readonly probes")
        for index, connection in enumerate(connections):
            with self.subTest(connection_index=index):
                self.assertEqual(connection.close_calls, 1)
                with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                    connection.execute("SELECT 1")

        # No source bytes need to be rewritten; journal head remains a
        # separate verification invariant even if SQLite WAL bookkeeping
        # changes the raw main-database file header across normal opens.
        with self.open() as source:
            self.assertEqual(source.count(), 1)
            self.assertEqual(source.verify_chain()["head"], original_head)


if __name__ == "__main__":
    unittest.main()
