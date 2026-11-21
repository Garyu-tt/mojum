"""Independent acceptance for bounded Noosphere JSON nesting.

All journals are disposable synthetic SQLite fixtures; no production history.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.noosphere import EMPTY_HEAD, EVENT_SCHEMA, LocalNoosphere, NoosphereError


def sample_event(payload):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": "offline-fixture-1",
        "agent_id": "test-agent",
        "topic": "synthetic",
        "observed_at_ns": 1,
        "recorded_at_ns": 1,
        "valid_from_ns": 0,
        "valid_to_ns": None,
        "visibility": "PRIVATE",
        "source": "fixture",
        "source_revision": "1",
        "payload": payload,
    }


def nested_payload(levels):
    value = "leaf"
    for _ in range(levels):
        value = [value]
    return {"nested": value}


class NoosphereJsonDepthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "test.sqlite3"
        self.store = LocalNoosphere(self.db, "test-agent")
        self.addCleanup(self.store.close)

    def test_deep_json_rejected_without_writes_and_valid_retry_succeeds(self):
        old_head = self.store.head()
        self.assertEqual(old_head, EMPTY_HEAD)
        with self.assertRaisesRegex(NoosphereError, "nesting limit"):
            self.store.append(sample_event(nested_payload(300)), expected_head=old_head)
        self.assertEqual(self.store.head(), old_head)
        self.assertEqual(self.store.count(), 0)
        head = self.store.append(sample_event(nested_payload(32)), expected_head=old_head)
        self.assertEqual(self.store.verify_chain(external_expected_head=head)["count"], 1)
        self.store.close()
        with LocalNoosphere(self.db, "test-agent") as reader:
            self.assertEqual(reader.verify_chain(external_expected_head=head)["count"], 1)

    def test_encoder_recursion_exception_is_contract_error(self):
        before = (self.store.head(), self.store.count())
        with patch("mojum.noosphere.json.dumps", side_effect=RecursionError("forced")):
            with self.assertRaises(NoosphereError):
                self.store.append(sample_event({"ok": True}), expected_head=before[0])
        self.assertEqual((self.store.head(), self.store.count()), before)

    def test_wide_shallow_payload_remains_supported(self):
        payload = {f"k{i}": "x" for i in range(1800)}
        head = self.store.append(sample_event(payload), expected_head=self.store.head())
        self.assertEqual(self.store.verify_chain(external_expected_head=head)["count"], 1)


if __name__ == "__main__":
    unittest.main()
