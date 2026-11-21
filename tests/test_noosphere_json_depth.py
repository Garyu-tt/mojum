"""Portable fail-closed depth limits for synthetic Noosphere SQLite journals."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


def fixture_event():
    return {
        "schema": EVENT_SCHEMA,
        "event_id": "depth-fixture-1",
        "agent_id": "depth-fixture-agent",
        "topic": "fixture",
        "observed_at_ns": 1,
        "recorded_at_ns": 1,
        "valid_from_ns": 0,
        "valid_to_ns": None,
        "visibility": "PRIVATE",
        "source": "mojum.test",
        "source_revision": "1",
        "payload": {"leaf": True},
    }


class NoosphereJsonDepthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "depth.sqlite3"
        self.store = LocalNoosphere(self.db, "depth-fixture-agent")
        self.addCleanup(self.store.close)

    def test_deep_dict_and_list_rejected_before_append(self):
        for container in ("dict", "list"):
            with self.subTest(container=container):
                nested = {"leaf": True}
                for _ in range(1200):
                    nested = {"next": nested} if container == "dict" else [nested]
                changed = fixture_event()
                changed["payload"] = {"nested": nested}
                prior = (self.store.head(), self.store.count())
                with self.assertRaisesRegex(NoosphereError, "nesting limit"):
                    self.store.append(changed, expected_head=prior[0])
                self.assertEqual((self.store.head(), self.store.count()), prior)
                self.assertEqual(self.store.verify_chain()["count"], 0)

    def test_moderately_nested_event_roundtrips_after_reopen(self):
        nested = {"leaf": "ok"}
        for _ in range(64):
            nested = {"next": nested}
        changed = fixture_event()
        changed["payload"] = nested
        head = self.store.append(changed, expected_head=self.store.head())
        self.assertEqual(self.store.verify_chain(external_expected_head=head)["count"], 1)
        self.store.close()
        with LocalNoosphere(self.db, "depth-fixture-agent") as recovered:
            self.assertEqual(recovered.verify_chain(external_expected_head=head)["count"], 1)
            items = recovered.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(items[0]["event"], changed)

    def test_runtime_encoder_recursion_is_contract_error(self):
        prior = self.store.head()
        with patch("mojum.noosphere.json.dumps", side_effect=RecursionError("mock encoder limit")):
            with self.assertRaisesRegex(NoosphereError, "canonical"):
                self.store.append(fixture_event(), expected_head=prior)
        self.assertEqual(self.store.head(), prior)
        self.assertEqual(self.store.count(), 0)


if __name__ == "__main__":
    unittest.main()
