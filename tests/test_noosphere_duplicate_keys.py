"""Persisted ``wire_json`` must be unambiguous: duplicate keys fail closed.

The event hash is computed over the *parsed* event, not over the exact stored
bytes.  A stored row that repeats an object key therefore parses to the same
object and keeps its hash valid, while two consumers may disagree about the
row (for example ``json_extract`` in SQLite versus ``json.loads`` in Python).
Legacy non-strict journals would accept such a row, so verification must
reject duplicate keys at the wire boundary instead of collapsing them.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import (
    EMPTY_HEAD, LocalNoosphere, NoosphereError, _read_first_event,
)

OWNER = "duplicate-key-fixture"

EVENT = {
    "schema": "mojum.noosphere.event/1",
    "event_id": "evt-1",
    "agent_id": OWNER,
    "topic": "test.duplicate-key",
    "observed_at_ns": 1,
    "recorded_at_ns": 1,
    "valid_from_ns": 1,
    "valid_to_ns": None,
    "visibility": "PUBLIC",
    "source": OWNER,
    "source_revision": "r1",
    "payload": {"value": 1},
}


class DuplicateJSONKeyJournalTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "journal.sqlite3"
        self.head = self._new_journal()

    def _new_journal(self):
        with LocalNoosphere(self.path, OWNER) as store:
            return store.append(EVENT, expected_head=EMPTY_HEAD)

    def _forge(self, raw, old, new):
        """Rewrite one stored row after dropping the append-only trigger.

        A local attacker with filesystem access can do this; the point is that
        the chain check must still notice the ambiguity without relying on the
        trigger.
        """
        self.assertIn(old, raw)
        forged = raw.replace(old, new, 1)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET wire_json = ? WHERE seq = 1", (forged,))
            db.commit()
        return forged

    def test_untampered_journal_verifies_unchanged(self):
        with LocalNoosphere(self.path, OWNER, read_only=True) as store:
            self.assertEqual(store.verify_chain()["head"], self.head)
        with LocalNoosphere(self.path, OWNER) as store:
            self.assertEqual(store.verify_chain()["head"], self.head)

    def test_duplicate_top_level_key_fails_writable_chain_check(self):
        raw = json.dumps(EVENT, sort_keys=True, separators=(",", ":"))
        forged = self._forge(
            raw, '"event_id":"evt-1"',
            '"event_id":"evt-1","event_id":"evt-1"',
        )
        # The ambiguity is real: both spellings parse to one object, so the
        # hash over the parsed event still matches the stored hash.
        self.assertEqual(json.loads(forged), json.loads(raw))
        with LocalNoosphere(self.path, OWNER) as store:
            self.assertEqual(store.head(), self.head)
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                store.verify_chain()

    def test_duplicate_nested_key_fails_writable_chain_check(self):
        raw = json.dumps(EVENT, sort_keys=True, separators=(",", ":"))
        forged = self._forge(
            raw, '"payload":{"value":1}',
            '"payload":{"value":1,"value":1}',
        )
        self.assertEqual(json.loads(forged), json.loads(raw))
        with LocalNoosphere(self.path, OWNER) as store:
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                store.verify_chain()

    def test_dataset_and_streaming_iteration_reject_ambiguous_row(self):
        raw = json.dumps(EVENT, sort_keys=True, separators=(",", ":"))
        self._forge(raw, '"event_id":"evt-1"', '"event_id":"evt-1","event_id":"evt-1"')
        with LocalNoosphere(self.path, OWNER) as store:
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                store.dataset(include_nonpublic=True)
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                list(store.iter_events(include_nonpublic=True))

    def test_bootstrap_discovery_rejects_ambiguous_row(self):
        raw = json.dumps(EVENT, sort_keys=True, separators=(",", ":"))
        self._forge(raw, '"event_id":"evt-1"', '"event_id":"evt-1","event_id":"evt-1"')
        with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
            _read_first_event(self.path, OWNER)


    def test_source_filter_cannot_hide_duplicate_keys(self):
        # SQLite json_extract reads the first duplicate key, while Python's
        # default json.loads reads the last. Filtered queries must validate
        # candidates before checking the source or applying their result cap.
        raw = json.dumps(EVENT, sort_keys=True, separators=(",", ":"))
        with LocalNoosphere(self.path, OWNER) as store:
            forged = self._forge(
                raw, '"source":"duplicate-key-fixture"',
                '"source":"decoy","source":"duplicate-key-fixture"',
            )
            self.assertEqual(json.loads(forged)["source"], OWNER)
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute(
                    "SELECT json_extract(wire_json, '$.source') FROM events"
                ).fetchone()[0], "decoy")
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                store.dataset(source=OWNER, include_nonpublic=True)
            with self.assertRaisesRegex(NoosphereError, "duplicate JSON key"):
                list(store.iter_events(source=OWNER, include_nonpublic=True))

    def test_source_filter_still_uses_post_filter_limit(self):
        with LocalNoosphere(self.path, OWNER) as store:
            next_event = dict(EVENT)
            next_event.update(event_id="evt-2", source="other", recorded_at_ns=2,
                              observed_at_ns=2, valid_from_ns=2)
            head = store.append(next_event, expected_head=self.head)
            self.assertEqual(store.verify_chain()["head"], head)
            self.assertEqual(len(store.dataset(source=OWNER, limit=1)["projection"]["items"]), 1)
            self.assertEqual(len(store.dataset(source="other", limit=1)["projection"]["items"]), 1)
            self.assertEqual(len(store.dataset(source="absent", limit=1)["projection"]["items"]), 0)
            self.assertEqual(len(list(store.iter_events(source=OWNER))), 1)
            self.assertEqual(len(list(store.iter_events(source="other"))), 1)


if __name__ == "__main__":
    unittest.main()
