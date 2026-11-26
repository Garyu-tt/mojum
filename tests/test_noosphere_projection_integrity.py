"""Projection candidates must agree with the indexes used to select them.

All records below are fabricated fixtures. PRIVATE/RESTRICTED labels exercise
the public-export boundary without reading any real private journal.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.noosphere import EMPTY_HEAD, EVENT_SCHEMA, LocalNoosphere, NoosphereError


OWNER = "projection-synthetic-fixture"
EVENT = {
    "schema": EVENT_SCHEMA,
    "event_id": "fixture-1",
    "agent_id": OWNER,
    "topic": "fixture.heartbeat",
    "observed_at_ns": 10,
    "recorded_at_ns": 20,
    "valid_from_ns": 5,
    "valid_to_ns": 30,
    "visibility": "PUBLIC",
    "source": "public-synthetic-fixture",
    "source_revision": "r1",
    "payload": {"beat": 1},
}


class ProjectionIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = LocalNoosphere(Path(temporary.name) / "fixture.sqlite3", OWNER)
        self.addCleanup(self.store.close)
        self.head = self.store.append(EVENT, expected_head=EMPTY_HEAD)
        # Simulate externally corrupted storage, bypassing the normal writer.
        self.store._db.execute("DROP TRIGGER noosphere_no_update")

    def _rewrite_event(self, **changes):
        event = copy.deepcopy(EVENT)
        event.update(changes)
        self.store._db.execute(
            "UPDATE events SET wire_json = ? WHERE seq = 1",
            (json.dumps(event),),
        )

    def _assert_both_reject(self, pattern, **filters):
        with self.assertRaisesRegex(NoosphereError, pattern):
            self.store.dataset(**filters)
        with self.assertRaisesRegex(NoosphereError, pattern):
            list(self.store.iter_events(**filters))

    def test_public_index_cannot_export_private_or_restricted_wire(self):
        for visibility in ("PRIVATE", "RESTRICTED"):
            with self.subTest(visibility=visibility):
                self._rewrite_event(visibility=visibility)
                self._assert_both_reject("indexed column visibility")

    def test_readonly_projection_rejects_corrupted_index_of_valid_private_event(self):
        private = dict(EVENT, event_id="fixture-private", visibility="PRIVATE")
        self.store.append(private, expected_head=self.head)
        self.store._db.execute(
            "UPDATE events SET visibility = 'PUBLIC' WHERE event_id = ?",
            (private["event_id"],),
        )
        path = self.store._db.execute("PRAGMA database_list").fetchone()["file"]
        with LocalNoosphere(path, OWNER, read_only=True) as reader:
            with self.assertRaisesRegex(NoosphereError, "indexed column visibility"):
                reader.dataset()
            with self.assertRaisesRegex(NoosphereError, "indexed column visibility"):
                list(reader.iter_events())

    def test_owner_index_cannot_export_another_agents_event(self):
        self._rewrite_event(agent_id="another-synthetic-agent")
        self._assert_both_reject("invalid event at row 1")

    def test_topic_index_cannot_admit_nonmatching_wire(self):
        self.store._db.execute("UPDATE events SET topic = 'fixture.other'")
        self._assert_both_reject("indexed column topic", topic="fixture.other")

    def test_recorded_index_cannot_admit_future_wire(self):
        self.store._db.execute("UPDATE events SET recorded_at_ns = 10")
        self._assert_both_reject("indexed column recorded_at_ns", as_of_recorded_ns=10)

    def test_validity_indexes_cannot_admit_outside_wire_interval(self):
        for column, value, instant in (("valid_from_ns", 0, 1),
                                       ("valid_to_ns", None, 31)):
            with self.subTest(column=column):
                # Column names come only from the fixed test cases above.
                self.store._db.execute(
                    f"UPDATE events SET {column} = ?", (value,),
                )
                self._assert_both_reject(f"indexed column {column}", valid_at_ns=instant)
                self.store._db.execute(
                    f"UPDATE events SET {column} = ?", (EVENT[column],),
                )

    def test_remaining_indexed_fields_must_match(self):
        for column, value in (("event_id", "changed-id"), ("observed_at_ns", 9)):
            with self.subTest(column=column):
                self.store._db.execute(
                    f"UPDATE events SET {column} = ?", (value,),
                )
                self._assert_both_reject(f"indexed column {column}")
                self.store._db.execute(
                    f"UPDATE events SET {column} = ?", (EVENT[column],),
                )

    def test_source_filter_does_not_hide_corrupt_candidate(self):
        self._rewrite_event(visibility="PRIVATE")
        self._assert_both_reject("indexed column visibility", source="absent-source")

    def test_explicit_nonpublic_export_still_checks_index_consistency(self):
        self._rewrite_event(visibility="PRIVATE")
        self._assert_both_reject("indexed column visibility", include_nonpublic=True)

    def test_malformed_wire_is_rejected_without_source_filter(self):
        for raw in ("[]", "null", '{"payload":{}}'):
            with self.subTest(raw=raw):
                self.store._db.execute("UPDATE events SET wire_json = ?", (raw,))
                self._assert_both_reject("invalid event at row 1")

    def test_invalid_wire_fields_are_rejected(self):
        for changes in ({"observed_at_ns": True}, {"payload": []},
                        {"source_revision": ""}, {"schema": "unsupported"}):
            with self.subTest(changes=changes):
                self._rewrite_event(**changes)
                self._assert_both_reject("invalid event at row 1")

    def test_valid_public_and_explicit_nonpublic_results_are_unchanged(self):
        original = self.store.dataset()
        private = dict(EVENT, event_id="fixture-2", recorded_at_ns=21,
                       visibility="PRIVATE", source="other-source")
        self.store.append(private, expected_head=self.head)
        self.assertEqual(self.store.dataset(), original)
        self.assertEqual(list(self.store.iter_events()), original["projection"]["items"])
        exported = self.store.dataset(include_nonpublic=True)["projection"]["items"]
        self.assertEqual([item["event"] for item in exported], [EVENT, private])
        self.assertEqual(list(self.store.iter_events(include_nonpublic=True,
                                                    batch_size=1)), exported)
        limited = self.store.dataset(include_nonpublic=True, source="other-source", limit=1)
        self.assertEqual(limited["projection"]["items"], exported[1:])

    def test_corrupt_candidate_is_not_yielded_after_valid_stream_prefix(self):
        second = dict(EVENT, event_id="fixture-2", recorded_at_ns=21)
        self.store.append(second, expected_head=self.head)
        self.store._db.execute("UPDATE events SET visibility = 'RESTRICTED' WHERE seq = 2")
        stream = self.store.iter_events(include_nonpublic=True, batch_size=1)
        self.addCleanup(stream.close)
        self.assertEqual(next(stream)["event"], EVENT)
        with self.assertRaisesRegex(NoosphereError, "indexed column visibility"):
            next(stream)


if __name__ == "__main__":
    unittest.main()
