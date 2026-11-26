"""Hermetic continuity and anti-tamper tests for synthetic GENESIS snapshots."""

import copy
import json
import unittest

from mojum.genesis import GenomeLedger
from mojum.genesis_append_witness import (
    GenesisWitnessError, compare_append_only,
)


def seed():
    return {
        "schema": "mojum.genesis.genotype/1", "genome_id": "synthetic-test",
        "revision": "fixture-r1", "inherited": {
            "heartbeat_bpm": 70,
            "body_scale_milli": 1_000,
            "sensory_gain_milli": 1_000,
        },
    }


def event(ident, gene="heartbeat_bpm", delta=1, at=1):
    return {
        "schema": "mojum.genesis.acquisition/1",
        "event_id": ident, "genome_id": "synthetic-test", "gene": gene,
        "delta": delta, "valid_from_tick": at,
        "observed_at_ns": at, "recorded_at_ns": at,
        "source": "synthetic-unit-test", "evidence": "not a measured animal",
    }


def snapshots():
    ledger = GenomeLedger(seed())
    empty = ledger.snapshot()
    ledger.append(event("first"), ledger.revision)
    one = ledger.snapshot()
    ledger.append(event("second", "sensory_gain_milli", -2, 2), ledger.revision)
    two = ledger.snapshot()
    return empty, one, two


def compare(a, b, classification="PUBLIC_SYNTHETIC"):
    return compare_append_only(a, b, classification=classification)


class GenesisAppendWitnessTests(unittest.TestCase):
    def test_valid_equal_pair(self):
        a, _, _ = snapshots()
        result = compare(a, a)
        self.assertEqual(result["relation"], "equal")
        self.assertEqual(result["appended_event_count"], 0)
        self.assertEqual(result["before_revision"], result["after_revision"])

    def test_valid_multi_event_append(self):
        a, _, c = snapshots()
        result = compare(a, c)
        self.assertEqual(result["relation"], "append_only")
        self.assertEqual(result["appended_event_count"], 2)
        self.assertEqual(result["appended_gene_counts"]["heartbeat_bpm"], 1)
        self.assertEqual(result["appended_gene_delta_sums"]["sensory_gain_milli"], -2)
        self.assertEqual(result["source_classification"], "PUBLIC_SYNTHETIC")

    def test_consistent_hashed_metadata_without_provenance_leaks(self):
        a, _, c = snapshots()
        result = compare(a, c)
        self.assertEqual(result, compare(copy.deepcopy(a), copy.deepcopy(c)))
        payload = json.dumps(result)
        for raw in ("first", "second", "synthetic-unit-test", "not a measured animal"):
            self.assertNotIn(raw, payload)

    def test_prefix_is_required_not_aggregate_equivalence(self):
        _, a, _ = snapshots()
        fork = GenomeLedger(seed())
        fork.append(event("different"), fork.revision)
        with self.assertRaisesRegex(GenesisWitnessError, "fork"):
            compare(a, fork.snapshot())

    def test_reordering_is_rejected(self):
        _, _, full = snapshots()
        altered = copy.deepcopy(full)
        altered["events"].reverse()
        with self.assertRaises(GenesisWitnessError):
            compare(full, altered)

    def test_rollback_is_rejected(self):
        _, a, b = snapshots()
        with self.assertRaisesRegex(GenesisWitnessError, "rolled back"):
            compare(b, a)

    def test_seed_mismatch_is_rejected_even_if_phenotype_matches(self):
        a, _, _ = snapshots()
        other = GenomeLedger({**seed(), "genome_id": "another"}).snapshot()
        with self.assertRaisesRegex(GenesisWitnessError, "seed"):
            compare(a, other)

    def test_corrupt_revision_is_rejected(self):
        a, _, b = snapshots()
        bad = copy.deepcopy(b)
        bad["revision"] = "00" * 32
        with self.assertRaisesRegex(GenesisWitnessError, "Invalid"):
            compare(a, bad)

    def test_refuses_private_and_missing_classification(self):
        a, _, _ = snapshots()
        for classification in (None, "PRIVATE", "public_synthetic", True):
            with self.subTest(classification=classification):
                with self.assertRaises(GenesisWitnessError):
                    compare(a, a, classification)

    def test_rejects_native_type_subclasses_before_equality(self):
        class EvilString(str):
            def __eq__(self, other):
                raise AssertionError("should never compare")
        a, _, _ = snapshots()
        altered = copy.deepcopy(a)
        altered["revision"] = EvilString(altered["revision"])
        with self.assertRaises(GenesisWitnessError):
            compare(altered, a)

    def test_rejects_bool_and_float_numbers(self):
        a, _, _ = snapshots()
        for injected in (True, 2.0):
            altered = copy.deepcopy(a)
            altered["seed"]["inherited"]["heartbeat_bpm"] = injected
            with self.subTest(injected=injected):
                with self.assertRaises(GenesisWitnessError):
                    compare(altered, a)

    def test_rejects_surrogate_and_enormous_input(self):
        a, _, _ = snapshots()
        for evil in ("\ud800", "x" * 1025):
            altered = copy.deepcopy(a)
            altered["seed"]["revision"] = evil
            with self.subTest(evil_len=len(evil)):
                with self.assertRaises(GenesisWitnessError):
                    compare(altered, a)

    def test_does_not_mutate_input_snapshots(self):
        a, _, c = snapshots()
        before_a, before_c = copy.deepcopy(a), copy.deepcopy(c)
        compare(a, c)
        self.assertEqual(a, before_a)
        self.assertEqual(c, before_c)

    def test_event_budget_precedes_replay(self):
        a, _, _ = snapshots()
        oversized = copy.deepcopy(a)
        oversized["events"] = [event(f"e-{i}") for i in range(129)]
        with self.assertRaisesRegex(GenesisWitnessError, "event budget"):
            compare(a, oversized)

    def test_sibling_records_do_not_gain_false_precedence(self):
        _, a, _ = snapshots()
        left = GenomeLedger(seed())
        left.append(event("first"), left.revision)
        left.append(event("left", "heartbeat_bpm", 2, 2), left.revision)
        right = GenomeLedger(seed())
        right.append(event("first"), right.revision)
        right.append(event("right", "heartbeat_bpm", 2, 2), right.revision)
        with self.assertRaisesRegex(GenesisWitnessError, "fork"):
            compare(left.snapshot(), right.snapshot())
        self.assertEqual(compare(a, left.snapshot())["appended_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
