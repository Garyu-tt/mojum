"""Adversarial tests for detached GENESIS acquisition batches."""
import copy
import json
import unittest
from pathlib import Path

from mojum.genesis import ACQUISITION_SCHEMA, GenesisError, GenomeLedger
from mojum.genesis_batch import (
    MAX_BATCH_EVENTS,
    BatchResult,
    apply_acquisition_batch,
)

SEED = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "genesis_seed.json").read_text()
)


def event(event_id="batch-a", *, tick=10, delta=1, recorded=200):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": event_id,
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": delta,
        "valid_from_tick": tick,
        "observed_at_ns": 100,
        "recorded_at_ns": recorded,
        "source": "test:batch",
        "evidence": "PUBLIC_SYNTHETIC fixture only",
    }


def apply(events, *, seed_snapshot=None):
    ledger = GenomeLedger(SEED)
    snapshot = ledger.snapshot() if seed_snapshot is None else seed_snapshot
    return apply_acquisition_batch(
        snapshot, events, expected_base_revision=ledger.revision
    )


class GenesisBatchTests(unittest.TestCase):
    def test_applies_multiple_events_in_recorded_order(self):
        result = apply([
            event("first", tick=10, delta=2, recorded=200),
            event("second", tick=20, delta=3, recorded=300),
        ])
        self.assertIsInstance(result, BatchResult)
        self.assertEqual(result.event_count, 2)
        self.assertEqual(len(result.event_revisions), 2)
        self.assertEqual(result.snapshot()["events"][0]["event_id"], "first")
        self.assertEqual(result.snapshot()["events"][1]["event_id"], "second")
        restored = GenomeLedger.from_snapshot(result.snapshot())
        self.assertEqual(restored.phenotype(9)["parameters"]["heartbeat_bpm"], 60)
        self.assertEqual(restored.phenotype(15)["parameters"]["heartbeat_bpm"], 62)
        self.assertEqual(restored.phenotype(25)["parameters"]["heartbeat_bpm"], 65)

    def test_returns_revision_receipt_for_each_append(self):
        base = GenomeLedger(SEED)
        result = apply_acquisition_batch(
            base.snapshot(),
            [event("a", recorded=200), event("b", recorded=300)],
            expected_base_revision=base.revision,
        )
        self.assertNotEqual(result.event_revisions[0], result.base_revision)
        self.assertEqual(result.event_revisions[-1], result.final_revision)
        self.assertEqual(result.event_revisions[-1], GenomeLedger.from_snapshot(result.snapshot()).revision)

    def test_does_not_mutate_caller_snapshot_or_events(self):
        ledger = GenomeLedger(SEED)
        snapshot = ledger.snapshot()
        events = [event("a", recorded=200), event("b", recorded=300)]
        before_snapshot = copy.deepcopy(snapshot)
        before_events = copy.deepcopy(events)
        apply_acquisition_batch(snapshot, events, expected_base_revision=ledger.revision)
        self.assertEqual(snapshot, before_snapshot)
        self.assertEqual(events, before_events)
        self.assertEqual(ledger.revision, before_snapshot["revision"])

    def test_stale_expected_base_revision_rejected(self):
        ledger = GenomeLedger(SEED)
        with self.assertRaisesRegex(GenesisError, "stale expected"):
            apply_acquisition_batch(
                ledger.snapshot(), [event()], expected_base_revision="0" * 64
            )

    def test_bad_expected_revision_type_and_length_rejected(self):
        ledger = GenomeLedger(SEED)
        for revision in (None, True, 1, "short"):
            with self.subTest(revision=revision):
                with self.assertRaises(GenesisError):
                    apply_acquisition_batch(
                        ledger.snapshot(), [event()], expected_base_revision=revision
                    )

    def test_duplicate_event_id_rejects_entire_batch(self):
        events = [event("same", recorded=200), event("same", recorded=300)]
        with self.assertRaisesRegex(GenesisError, "Duplicate"):
            apply(events)

    def test_non_monotonic_recording_clock_rejects_entire_batch(self):
        events = [event("later", recorded=300), event("earlier", recorded=200)]
        with self.assertRaisesRegex(GenesisError, "monotonic"):
            apply(events)

    def test_second_event_out_of_bounds_rejects_batch(self):
        events = [
            event("valid-first", delta=1, recorded=200),
            event("invalid-second", delta=200, recorded=300),
        ]
        with self.assertRaisesRegex(GenesisError, "exceeds"):
            apply(events)

    def test_intermediate_tick_prefix_is_validated(self):
        # A later-recorded event may become effective at an earlier tick and
        # make a previously valid future projection invalid.
        events = [
            event("future", tick=20, delta=100, recorded=200),
            event("earlier", tick=10, delta=30, recorded=300),
        ]
        with self.assertRaisesRegex(GenesisError, "exceeds"):
            apply(events)

    def test_wrong_genome_and_unknown_gene_rejected(self):
        wrong_genome = event()
        wrong_genome["genome_id"] = "another-organism"
        unknown_gene = event("unknown", recorded=300)
        unknown_gene["gene"] = "invented"
        for bad in (wrong_genome, unknown_gene):
            with self.subTest(bad=bad["event_id"]):
                with self.assertRaises(GenesisError):
                    apply([bad])

    def test_bool_and_float_event_numbers_rejected(self):
        for key, value in (("delta", True), ("valid_from_tick", 2.0), ("recorded_at_ns", float("nan"))):
            bad = event()
            bad[key] = value
            with self.subTest(key=key):
                with self.assertRaises(GenesisError):
                    apply([bad])

    def test_native_containers_required_at_all_levels(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        ledger = GenomeLedger(SEED)
        bad_snapshot = DictSubclass(ledger.snapshot())
        bad_events = ListSubclass([event()])
        with self.assertRaises(GenesisError):
            apply_acquisition_batch(bad_snapshot, [event()], expected_base_revision=ledger.revision)
        with self.assertRaises(GenesisError):
            apply_acquisition_batch(ledger.snapshot(), bad_events, expected_base_revision=ledger.revision)

        hostile_event = DictSubclass(event())
        with self.assertRaises(GenesisError):
            apply_acquisition_batch(ledger.snapshot(), [hostile_event], expected_base_revision=ledger.revision)

    def test_bad_snapshot_revision_rejected(self):
        ledger = GenomeLedger(SEED)
        snapshot = ledger.snapshot()
        snapshot["revision"] = "f" * 64
        with self.assertRaises(GenesisError):
            apply_acquisition_batch(snapshot, [event()], expected_base_revision=ledger.revision)

    def test_empty_and_oversized_batches_rejected(self):
        ledger = GenomeLedger(SEED)
        with self.assertRaisesRegex(GenesisError, "1.."):
            apply_acquisition_batch(ledger.snapshot(), [], expected_base_revision=ledger.revision)
        too_many = [event(f"e-{i}", delta=0, recorded=100 + i) for i in range(MAX_BATCH_EVENTS + 1)]
        with self.assertRaisesRegex(GenesisError, "1.."):
            apply_acquisition_batch(ledger.snapshot(), too_many, expected_base_revision=ledger.revision)

    def test_oversized_event_rejected(self):
        bad = event()
        bad["evidence"] = "x" * 9000
        with self.assertRaises(GenesisError):
            apply([bad])

    def test_invalid_unicode_rejected(self):
        bad = event()
        bad["evidence"] = "bad-\ud800"
        with self.assertRaisesRegex(GenesisError, "UTF-8"):
            apply([bad])

    def test_deterministic_digest_and_receipts(self):
        first = apply([event("a", recorded=200), event("b", recorded=300)])
        second = apply([event("a", recorded=200), event("b", recorded=300)])
        self.assertEqual(first.batch_sha256, second.batch_sha256)
        self.assertEqual(first.event_revisions, second.event_revisions)
        self.assertEqual(first.snapshot_json, second.snapshot_json)

    def test_event_order_is_semantically_preserved(self):
        a = apply([event("a", tick=10, delta=1, recorded=200), event("b", tick=20, delta=2, recorded=300)])
        b = apply([event("b", tick=20, delta=2, recorded=200), event("a", tick=10, delta=1, recorded=300)])
        self.assertNotEqual(a.batch_sha256, b.batch_sha256)

    def test_returned_snapshot_is_detached_on_each_read(self):
        result = apply([event()])
        one = result.snapshot()
        one["events"][0]["delta"] = 99
        two = result.snapshot()
        self.assertEqual(two["events"][0]["delta"], 1)

    def test_snapshot_input_type_is_checked(self):
        ledger = GenomeLedger(SEED)
        for snapshot in (None, [], "json", 1):
            with self.subTest(kind=type(snapshot).__name__):
                with self.assertRaises(GenesisError):
                    apply_acquisition_batch(snapshot, [event()], expected_base_revision=ledger.revision)


if __name__ == "__main__":
    unittest.main()
