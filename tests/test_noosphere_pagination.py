"""Regressions for stable, PUBLIC-only Noosphere keyset pagination."""
from __future__ import annotations
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import LocalNoosphere, NoosphereError
from mojum.noosphere_pagination import CURSOR_SCHEMA, PAGE_SCHEMA, PaginationError, page_public_dataset


class NoospherePaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.sqlite3"
        self.agent = "pagination-fixture"
        with LocalNoosphere(self.path, self.agent) as store:
            head = store.head()
            for n in range(1, 8):
                head = store.append(self._event(n, visibility="PRIVATE" if n == 3 else "PUBLIC"),
                                    expected_head=head)
            store.append(self._event(8, topic="other", source="sensor-b"), expected_head=head)

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, n, *, visibility="PUBLIC", topic="sensor", source="sim"):
        return {"schema": "mojum.noosphere.event/1", "event_id": f"event-{n}",
                "agent_id": self.agent, "topic": topic, "observed_at_ns": n * 10,
                "recorded_at_ns": n * 10, "valid_from_ns": n * 10, "valid_to_ns": None,
                "visibility": visibility, "source": source, "source_revision": "fixture-r1",
                "payload": {"value": n}}

    def _reader(self):
        return LocalNoosphere(self.path, self.agent, read_only=True)

    def test_pages_are_ordered_exhaustive_and_public_only(self):
        with self._reader() as store:
            first = page_public_dataset(store, page_size=2)
            self.assertEqual(first["page"]["schema"], PAGE_SCHEMA)
            self.assertEqual(first["page"]["snapshot"]["count"], 8)
            self.assertEqual([x["seq"] for x in first["page"]["items"]], [1, 2])
            items, cursor = list(first["page"]["items"]), first["page"]["next_cursor"]
            while cursor is not None:
                page = page_public_dataset(store, cursor=cursor)["page"]
                items.extend(page["items"])
                cursor = page["next_cursor"]
            self.assertEqual([x["seq"] for x in items], [1, 2, 4, 5, 6, 7, 8])
            self.assertTrue(all(x["event"]["visibility"] == "PUBLIC" for x in items))

    def test_source_topic_recorded_and_validity_filters(self):
        with self._reader() as store:
            page = page_public_dataset(store, topic="sensor", source="sim",
                                       as_of_recorded_ns=50, valid_at_ns=40,
                                       page_size=10)["page"]
            self.assertEqual([x["event"]["payload"]["value"] for x in page["items"]], [1, 2, 4])
            self.assertEqual(page["filters"]["visibility"], "PUBLIC")
            self.assertIsNone(page["next_cursor"])

    def test_appends_after_first_page_are_excluded(self):
        reader = self._reader()
        try:
            first = page_public_dataset(reader, page_size=2)["page"]
            cursor, seen = first["next_cursor"], [x["seq"] for x in first["items"]]
            with LocalNoosphere(self.path, self.agent) as writer:
                writer.append(self._event(9), expected_head=writer.head())
            while cursor is not None:
                page = page_public_dataset(reader, cursor=cursor)["page"]
                seen.extend(x["seq"] for x in page["items"])
                cursor = page["next_cursor"]
            self.assertEqual(seen, [1, 2, 4, 5, 6, 7, 8])
        finally:
            reader.close()

    def test_cursor_tampering_and_schema_changes_fail_closed(self):
        with self._reader() as store:
            cursor = page_public_dataset(store, page_size=2)["page"]["next_cursor"]
            for changed in (dict(cursor, after_seq=3),
                            dict(cursor, schema=CURSOR_SCHEMA + "/forged")):
                with self.assertRaises(PaginationError):
                    page_public_dataset(store, cursor=changed)

    def test_page_size_and_filters_cannot_change(self):
        with self._reader() as store:
            cursor = page_public_dataset(store, page_size=2, topic="sensor")["page"]["next_cursor"]
            with self.assertRaises(PaginationError):
                page_public_dataset(store, cursor=cursor, page_size=3)
            with self.assertRaises(PaginationError):
                page_public_dataset(store, cursor=cursor, topic="other")

    def test_external_anchor_only_applies_to_first_page(self):
        with self._reader() as store:
            head = store.head()
            page = page_public_dataset(store, page_size=1, expected_snapshot_head=head)["page"]
            with self.assertRaises(PaginationError):
                page_public_dataset(store, cursor=page["next_cursor"], expected_snapshot_head=head)
            with self.assertRaises(PaginationError):
                page_public_dataset(store, expected_snapshot_head="f" * 64)

    def test_requires_read_only_store_and_no_caller_transaction(self):
        with LocalNoosphere(self.path, self.agent) as writer:
            with self.assertRaises(PaginationError):
                page_public_dataset(writer)
        reader = self._reader()
        try:
            reader._db.execute("BEGIN")
            with self.assertRaises(PaginationError):
                page_public_dataset(reader)
            reader._db.execute("ROLLBACK")
        finally:
            reader.close()

    def test_shortened_history_is_detected_on_resume(self):
        reader = self._reader()
        try:
            cursor = page_public_dataset(reader, page_size=1)["page"]["next_cursor"]
        finally:
            reader.close()
        raw = sqlite3.connect(self.path)
        try:
            raw.execute("DROP TRIGGER noosphere_no_delete")
            raw.execute("DELETE FROM events WHERE seq > 5")
            raw.commit()
        finally:
            raw.close()
        with self._reader() as store:
            with self.assertRaises((PaginationError, NoosphereError)):
                page_public_dataset(store, cursor=cursor)

    def test_malformed_arguments_are_rejected(self):
        with self._reader() as store:
            for value in (True, 0, -1, 1001, 1.5):
                with self.subTest(page_size=value), self.assertRaises(PaginationError):
                    page_public_dataset(store, page_size=value)
            with self.assertRaises(PaginationError):
                page_public_dataset(store, topic="\ud800")
            with self.assertRaises(PaginationError):
                page_public_dataset(store, as_of_recorded_ns=True)


if __name__ == "__main__":
    unittest.main()
