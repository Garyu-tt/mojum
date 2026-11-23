"""PUBLIC-only Noosphere export-witness regression tests."""
import copy
import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError
from mojum.noosphere_export_witness import (
    EXPORT_WITNESS_SCHEMA, create_public_export_witness,
)


def make_event(event_id, *, visibility="PUBLIC", topic="heartbeat",
               source="fixture", recorded=20, valid_from=0, valid_to=None):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": event_id,
        "agent_id": "witness-test",
        "topic": topic,
        "observed_at_ns": recorded - 1,
        "recorded_at_ns": recorded,
        "valid_from_ns": valid_from,
        "valid_to_ns": valid_to,
        "visibility": visibility,
        "source": source,
        "source_revision": "synthetic-1",
        "payload": {"value": event_id},
    }


class ExportWitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "journal.sqlite"
        self.store = LocalNoosphere(self.path, "witness-test")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def append(self, item):
        return self.store.append(item, expected_head=self.store.head())

    def test_witness_binds_verified_public_snapshot(self):
        self.append(make_event("public-1"))
        self.append(make_event("private-1", visibility="PRIVATE", recorded=30))
        self.append(make_event("restricted-1", visibility="RESTRICTED", recorded=40))
        head = self.store.head()

        witness = create_public_export_witness(self.store, expected_head=head)
        receipt = witness["receipt"]
        self.assertEqual(receipt["schema"], EXPORT_WITNESS_SCHEMA)
        self.assertEqual(receipt["source_head"], head)
        self.assertEqual(receipt["source_count"], 3)
        self.assertEqual(receipt["visibility"], "PUBLIC")
        self.assertEqual(receipt["exported_count"], 1)
        self.assertTrue(receipt["external_anchor_verified"])
        self.assertEqual(len(witness["sha256"]), 64)

    def test_receipt_is_deterministic_for_same_snapshot_and_query(self):
        self.append(make_event("one"))
        kwargs = {"filters": {"topic": "heartbeat"}, "limit": 10,
                  "expected_head": self.store.head()}
        first = create_public_export_witness(self.store, **kwargs)
        second = create_public_export_witness(self.store, **kwargs)
        self.assertEqual(first, second)

    def test_filters_and_limit_are_bound_into_receipt(self):
        self.append(make_event("a", topic="heartbeat"))
        self.append(make_event("b", topic="world", recorded=30))
        witness = create_public_export_witness(
            self.store, filters={"topic": "world", "as_of_recorded_ns": 30},
            limit=7,
        )["receipt"]
        self.assertEqual(witness["exported_count"], 1)
        self.assertEqual(witness["filters"]["topic"], "world")
        self.assertEqual(witness["filters"]["as_of_recorded_ns"], 30)
        self.assertEqual(witness["filters"]["include_nonpublic"], False)
        self.assertEqual(witness["filters"]["limit"], 7)

    def test_visibility_never_becomes_nonpublic_even_if_requested(self):
        self.append(make_event("public"))
        self.append(make_event("private", visibility="PRIVATE", recorded=30))
        with self.assertRaisesRegex(NoosphereError, "unsupported export filter"):
            create_public_export_witness(
                self.store, filters={"include_nonpublic": True},
            )

    def test_wrong_external_anchor_fails_closed(self):
        self.append(make_event("public"))
        with self.assertRaisesRegex(NoosphereError, "external anchor"):
            create_public_export_witness(self.store, expected_head="0" * 64)

    def test_malformed_external_anchor_rejected_before_transaction(self):
        for anchor in ("A" * 64, "0" * 63, "g" * 64, True):
            with self.subTest(anchor=anchor):
                before = self.store._db.in_transaction
                with self.assertRaises(NoosphereError):
                    create_public_export_witness(self.store, expected_head=anchor)
                self.assertEqual(self.store._db.in_transaction, before)

    def test_bad_filter_shapes_and_bool_timestamps_rejected(self):
        for filters in (
            [], "topic=heartbeat", {"unknown": "x"},
            {"valid_at_ns": True}, {"source": ""}, {"topic": "x" * 257},
            {"topic": "\ud800"},
        ):
            with self.subTest(filters=repr(filters)):
                with self.assertRaises(NoosphereError):
                    create_public_export_witness(self.store, filters=filters)
        self.assertFalse(self.store._db.in_transaction)

    def test_hostile_string_subclass_filter_key_rejected_before_comparison(self):
        class HostileKey(str):
            def __hash__(self):
                return str.__hash__(self)
            def __eq__(self, other):
                raise RuntimeError("must not compare hostile key")
        with self.assertRaises(NoosphereError):
            create_public_export_witness(self.store, filters={HostileKey("topic"): "x"})
        self.assertFalse(self.store._db.in_transaction)

    def test_limit_type_and_bounds(self):
        for value in (True, 0, -1, 10_001, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaises(NoosphereError):
                    create_public_export_witness(self.store, limit=value)

    def test_rejects_noncanonical_store_subclass(self):
        class StoreSubclass(LocalNoosphere):
            pass
        with self.assertRaises(NoosphereError):
            create_public_export_witness(StoreSubclass.__new__(StoreSubclass))

    def test_rejects_call_inside_existing_transaction_without_interference(self):
        self.store._db.execute("BEGIN")
        try:
            with self.assertRaisesRegex(NoosphereError, "idle journal connection"):
                create_public_export_witness(self.store)
            self.assertTrue(self.store._db.in_transaction)
        finally:
            self.store._db.execute("ROLLBACK")

    def test_receipt_does_not_mutate_journal_or_input_filters(self):
        self.append(make_event("one"))
        filters = {"source": "fixture"}
        before = (self.store.head(), self.store.count())
        first = create_public_export_witness(self.store, filters=filters)
        self.assertEqual(filters, {"source": "fixture"})
        self.assertEqual((self.store.head(), self.store.count()), before)
        first["receipt"]["filters"]["topic"] = "tampered"
        second = create_public_export_witness(self.store, filters=filters)
        self.assertIsNone(second["receipt"]["filters"]["topic"])

    def test_append_after_receipt_yields_new_snapshot_receipt(self):
        self.append(make_event("first"))
        first = create_public_export_witness(self.store)
        self.append(make_event("second", recorded=30))
        second = create_public_export_witness(self.store)
        self.assertNotEqual(first["receipt"]["source_head"], second["receipt"]["source_head"])
        self.assertEqual(first["receipt"]["source_count"], 1)
        self.assertEqual(second["receipt"]["source_count"], 2)

    def test_cold_reopen_reproduces_same_receipt(self):
        self.append(make_event("cold"))
        anchor = self.store.head()
        first = create_public_export_witness(self.store, expected_head=anchor)
        self.store.close()
        self.store = LocalNoosphere(self.path, "witness-test", read_only=True)
        second = create_public_export_witness(self.store, expected_head=anchor)
        self.assertEqual(first, second)

    def test_corrupt_chain_prevents_receipt(self):
        self.append(make_event("tamper"))
        self.store._db.execute("DROP TRIGGER noosphere_no_update")
        self.store._db.execute("UPDATE events SET topic='forged' WHERE seq=1")
        with self.assertRaises(NoosphereError):
            create_public_export_witness(self.store)
        self.assertFalse(self.store._db.in_transaction)

    def test_projection_digest_mismatch_rejected(self):
        self.append(make_event("one"))
        original = self.store.dataset
        def forged_dataset(**kwargs):
            result = original(**kwargs)
            result["sha256"] = "0" * 64
            return result
        self.store.dataset = forged_dataset
        try:
            with self.assertRaisesRegex(NoosphereError, "digest mismatch"):
                create_public_export_witness(self.store)
        finally:
            del self.store.dataset

    def test_dataset_visibility_scope_is_checked(self):
        self.append(make_event("public"))
        original = self.store.dataset
        def forged_dataset(**kwargs):
            result = original(**kwargs)
            result["projection"]["items"][0]["event"]["visibility"] = "PRIVATE"
            return result
        self.store.dataset = forged_dataset
        try:
            with self.assertRaisesRegex(NoosphereError, "non-PUBLIC"):
                create_public_export_witness(self.store)
        finally:
            del self.store.dataset


if __name__ == "__main__":
    unittest.main()
