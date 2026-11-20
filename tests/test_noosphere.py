"""Noosphere R0.1 offline-first SQLite integration tests; Python stdlib only."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


def event(event_id="ev1", *, owner="dante-synthetic", topic="heartbeat", observed=10, recorded=20, valid_from=0, valid_to=None, visibility="PUBLIC", payload=None):
    return {
        "schema": EVENT_SCHEMA, "event_id": event_id, "agent_id": owner,
        "topic": topic, "observed_at_ns": observed, "recorded_at_ns": recorded,
        "valid_from_ns": valid_from, "valid_to_ns": valid_to,
        "visibility": visibility, "source": "fixture:heart", "source_revision": "synthetic-0.1",
        "payload": {"beat": 1} if payload is None else payload,
    }


class NoosphereTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agent.sqlite"
        self.store = LocalNoosphere(self.path, "dante-synthetic")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_creation_is_persistent_on_disk(self):
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.store.count(), 0)

    def test_append_and_reopen(self):
        head = self.store.append(event(), expected_head=self.store.head())
        self.store.close()
        self.store = LocalNoosphere(self.path, "dante-synthetic")
        self.assertEqual(self.store.head(), head)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.dataset()["projection"]["items"][0]["event"]["event_id"], "ev1")

    def test_append_does_not_mutate_caller(self):
        e = event()
        old = copy.deepcopy(e)
        self.store.append(e, expected_head=self.store.head())
        self.assertEqual(e, old)
        e["payload"]["beat"] = 999
        self.assertEqual(self.store.dataset()["projection"]["items"][0]["event"]["payload"]["beat"], 1)

    def test_duplicate_id_and_transaction_rollback(self):
        self.store.append(event(), expected_head=self.store.head())
        first = self.store.head()
        with self.assertRaises(NoosphereError):
            self.store.append(event(), expected_head=first)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.head(), first)

    def test_compare_and_swap_stale_rejected(self):
        old = self.store.head()
        self.store.append(event(), expected_head=old)
        with self.assertRaisesRegex(NoosphereError, "CAS failed"):
            self.store.append(event("ev2", observed=30, recorded=40), expected_head=old)

    def test_different_agent_rejected(self):
        with self.assertRaises(NoosphereError):
            self.store.append(event(owner="other"), expected_head=self.store.head())
        with self.assertRaises(NoosphereError):
            LocalNoosphere(self.path, "other")

    def test_symbolic_link_db_path_rejected(self):
        link = Path(self.tmp.name) / "alias.sqlite"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        with self.assertRaises(NoosphereError):
            LocalNoosphere(link, "dante-synthetic")

    def test_unknown_keys_rejected(self):
        e = event()
        e["code"] = "exec(...)"
        with self.assertRaises(NoosphereError):
            self.store.append(e, expected_head=self.store.head())

    def test_non_json_payload_rejected(self):
        for payload in ({"x": float("nan")}, {"x": (1,2)}, {"x": {1:"foo"}}):
            with self.assertRaises(NoosphereError):
                self.store.append(event(payload=payload), expected_head=self.store.head())

    def test_bool_timestamp_rejected(self):
        with self.assertRaises(NoosphereError):
            self.store.append(event(observed=True), expected_head=self.store.head())

    def test_causality_and_valid_range(self):
        with self.assertRaises(NoosphereError):
            self.store.append(event(observed=30, recorded=20), expected_head=self.store.head())
        with self.assertRaises(NoosphereError):
            self.store.append(event(valid_from=20, valid_to=20), expected_head=self.store.head())

    def test_nonmonotonic_recorded_time_rejected(self):
        self.store.append(event(), expected_head=self.store.head())
        with self.assertRaises(NoosphereError):
            self.store.append(event("ev2", observed=1, recorded=19), expected_head=self.store.head())

    def test_chain_validates_and_external_anchor(self):
        self.store.append(event(), expected_head=self.store.head())
        anchor = self.store.head()
        v = self.store.verify_chain(external_expected_head=anchor)
        self.assertEqual(v["count"], 1)
        with self.assertRaises(NoosphereError):
            self.store.verify_chain(external_expected_head="wrong")

    def test_sqlite_update_delete_triggers(self):
        self.store.append(event(), expected_head=self.store.head())
        with self.assertRaises(sqlite3.DatabaseError):
            self.store._db.execute("UPDATE events SET topic = 'hacked' WHERE seq = 1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store._db.execute("DELETE FROM events WHERE seq = 1")
        self.assertEqual(self.store.count(), 1)

    def test_private_entries_not_exported_by_default(self):
        h = self.store.append(event("public"), expected_head=self.store.head())
        self.store.append(event("private", observed=30, recorded=40, visibility="PRIVATE"), expected_head=h)
        self.assertEqual([x["event"]["event_id"] for x in self.store.dataset()["projection"]["items"]], ["public"])
        self.assertEqual(len(self.store.dataset(include_nonpublic=True)["projection"]["items"]), 2)

    def test_deterministic_query_and_read_does_not_mutate(self):
        self.store.append(event(), expected_head=self.store.head())
        a = self.store.dataset()
        b = self.store.dataset()
        self.assertEqual(a, b)
        a["projection"]["items"][0]["event"]["payload"]["beat"] = 999
        self.assertEqual(self.store.dataset(), b)
        self.assertEqual(self.store.count(), 1)

    def test_as_of_recorded_and_valid_time_queries(self):
        self.store.append(event("a", recorded=20, valid_from=3, valid_to=9), expected_head=self.store.head())
        self.store.append(event("b", observed=20, recorded=40, valid_from=9), expected_head=self.store.head())
        self.assertEqual(len(self.store.dataset(as_of_recorded_ns=20)["projection"]["items"]), 1)
        self.assertEqual(self.store.dataset(valid_at_ns=5)["projection"]["items"][0]["event"]["event_id"], "a")
        self.assertEqual(self.store.dataset(valid_at_ns=9)["projection"]["items"][0]["event"]["event_id"], "b")

    def test_non_string_visibility_rejected_as_contract_error(self):
        e = event()
        e["visibility"] = []
        with self.assertRaises(NoosphereError):
            self.store.append(e, expected_head=self.store.head())

    def test_indexed_column_corruption_detected(self):
        self.store.append(event(), expected_head=self.store.head())
        self.store._db.execute("DROP TRIGGER noosphere_no_update")
        self.store._db.execute("UPDATE events SET visibility = 'PRIVATE' WHERE seq = 1")
        with self.assertRaisesRegex(NoosphereError, "indexed column visibility"):
            self.store.verify_chain()

    def test_deleted_row_sequence_detected(self):
        self.store.append(event(), expected_head=self.store.head())
        self.store.append(event("ev2", observed=30, recorded=40), expected_head=self.store.head())
        self.store._db.execute("DROP TRIGGER noosphere_no_delete")
        self.store._db.execute("DELETE FROM events WHERE seq = 1")
        with self.assertRaisesRegex(NoosphereError, "noncontiguous sequence"):
            self.store.verify_chain()

    def test_topic_filter_and_limit(self):
        self.store.append(event("a"), expected_head=self.store.head())
        self.store.append(event("b", topic="world", observed=30, recorded=40), expected_head=self.store.head())
        self.assertEqual(len(self.store.dataset(topic="heartbeat")["projection"]["items"]), 1)
        self.assertEqual(len(self.store.dataset(limit=1)["projection"]["items"]), 1)

    def test_source_filter_and_provenance(self):
        first = event("a")
        second = event("b", observed=30, recorded=40)
        second["source"] = "world.fixture"
        self.store.append(first, expected_head=self.store.head())
        self.store.append(second, expected_head=self.store.head())
        view = self.store.dataset(source="world.fixture")
        self.assertEqual([r["event"]["event_id"] for r in view["projection"]["items"]], ["b"])
        self.assertEqual(view["projection"]["filters"]["source"], "world.fixture")

    def test_invalid_dataset_parameters_fail(self):
        for kwargs in ({"limit": 0}, {"limit": True}, {"include_nonpublic": "yes"}, {"valid_at_ns": -1}, {"source": ""}):
            with self.assertRaises(NoosphereError):
                self.store.dataset(**kwargs)

    def test_two_connections_cas_consistency(self):
        other = LocalNoosphere(self.path, "dante-synthetic")
        try:
            old = self.store.head()
            other.append(event("a"), expected_head=old)
            with self.assertRaises(NoosphereError):
                self.store.append(event("b", observed=30, recorded=40), expected_head=old)
            self.assertEqual(self.store.count(), 1)
        finally:
            other.close()

    def test_oversize_event_rejected(self):
        huge = event(payload={"x": "0" * 200_000})
        with self.assertRaises(NoosphereError):
            self.store.append(huge, expected_head=self.store.head())


if __name__ == "__main__":
    unittest.main()
