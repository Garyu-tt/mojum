"""Fail-closed UTF-8 contracts for the real stdlib SQLite Noosphere.

These are disposable synthetic journals, never user or production data.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


def event():
    return {
        "schema": EVENT_SCHEMA,
        "event_id": "fixture-1",
        "agent_id": "dante-synthetic",
        "topic": "art",
        "observed_at_ns": 1,
        "recorded_at_ns": 1,
        "valid_from_ns": 0,
        "valid_to_ns": None,
        "visibility": "PRIVATE",
        "source": "synthetic.fixture",
        "source_revision": "1",
        "payload": {"lesson": "drawing"},
    }


class NoosphereUnicodeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite3"
        self.store = LocalNoosphere(self.path, "dante-synthetic")
        self.addCleanup(self.store.close)

    def assert_rejected_without_append(self, changed):
        before = (self.store.head(), self.store.count())
        with self.assertRaises(NoosphereError):
            self.store.append(changed, expected_head=before[0])
        self.assertEqual((self.store.head(), self.store.count()), before)
        self.assertEqual(self.store.verify_chain()["count"], 0)

    def test_owner_with_unpaired_surrogate_refused_before_sqlite_creation(self):
        for name in ("\ud800", "owner-\udfff", "\ud800ok"):
            with self.subTest(name=ascii(name)):
                other = Path(self.temp.name) / "unexpected.sqlite3"
                with self.assertRaisesRegex(NoosphereError, "UTF-8"):
                    LocalNoosphere(other, name)
                self.assertFalse(other.exists())

    def test_all_event_text_fields_reject_unpaired_surrogates(self):
        for field in ("event_id", "topic", "source", "source_revision"):
            with self.subTest(field=field):
                changed = event()
                changed[field] = "prefix-\ud800-suffix"
                self.assert_rejected_without_append(changed)

    def test_payload_nested_value_and_key_rejected_atomically(self):
        for payload in (
            {"nested": {"name": "bad\udc00"}},
            {"nested": {"\ud800": "key"}},
            {"notes": ["ok", "bad\udfff"]},
        ):
            with self.subTest(payload=ascii(payload)):
                changed = event()
                changed["payload"] = payload
                self.assert_rejected_without_append(changed)

    def test_invalid_dataset_filter_is_contract_error(self):
        before = (self.store.head(), self.store.count())
        for kwargs in ({"topic": "\ud800"}, {"source": "bad\udfff"}):
            with self.subTest(kwargs=ascii(kwargs)):
                with self.assertRaisesRegex(NoosphereError, "UTF-8"):
                    self.store.dataset(**kwargs)
        self.assertEqual((self.store.head(), self.store.count()), before)

    def test_deeply_nested_payload_is_not_raw_recursion_error(self):
        nested = {"leaf": True}
        for _ in range(1200):
            nested = {"deeper": nested}
        changed = event()
        changed["payload"] = nested
        self.assert_rejected_without_append(changed)

    def test_deeply_nested_list_is_rejected_before_sqlite_append(self):
        nested = "leaf"
        for _ in range(1200):
            nested = [nested]
        changed = event()
        changed["payload"] = {"nested": nested}
        self.assert_rejected_without_append(changed)

    def test_moderately_nested_json_remains_supported(self):
        nested = {"leaf": "ok"}
        for _ in range(64):
            nested = {"next": nested}
        changed = event()
        changed["payload"] = nested
        anchor = self.store.append(changed, expected_head=self.store.head())
        self.assertEqual(self.store.verify_chain(external_expected_head=anchor)["count"], 1)

    def test_valid_cyrillic_and_emoji_still_roundtrip_without_mutation(self):
        changed = event()
        changed["event_id"] = "урок-🎨"
        changed["topic"] = "рисование"
        changed["payload"] = {"набросок": "цветной 🎨"}
        before = dict(changed)
        anchor = self.store.append(changed, expected_head=self.store.head())
        self.assertEqual(changed, before)
        self.assertEqual(self.store.verify_chain(external_expected_head=anchor)["count"], 1)
        projection = self.store.dataset(include_nonpublic=True)
        self.assertEqual(projection["projection"]["items"][0]["event"], changed)
        self.store.close()
        with LocalNoosphere(self.path, "dante-synthetic") as reopened:
            self.assertEqual(reopened.verify_chain(external_expected_head=anchor)["count"], 1)
            self.assertEqual(reopened.dataset(include_nonpublic=True), projection)


if __name__ == "__main__":
    unittest.main()
