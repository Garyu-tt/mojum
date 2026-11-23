import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from mojum.genesis import ACQUISITION_SCHEMA, GenomeLedger, canonical as genesis_canonical
from mojum.genesis_phenotype_audit import (
    GenesisPhenotypeAuditError, SCHEMA, audit_phenotype,
)

SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json")
    .read_text(encoding="utf-8")
)


def make_ledger():
    ledger = GenomeLedger(SEED)
    event = {
        "schema": ACQUISITION_SCHEMA, "event_id": "event-a",
        "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm",
        "delta": 5, "valid_from_tick": 3, "observed_at_ns": 10,
        "recorded_at_ns": 20, "source": "test:synthetic",
        "evidence": "synthetic fixture only",
    }
    ledger.append(event, ledger.revision)
    return ledger


class PhenotypeAuditTests(unittest.TestCase):
    def test_matching_projection_is_attested(self):
        ledger = make_ledger()
        result = audit_phenotype(ledger.snapshot(), ledger.phenotype(3), 3)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["mismatched_fields"], [])
        self.assertEqual(result["ledger_revision"], ledger.revision)

    def test_mismatch_reports_only_field_names(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        observed["parameters"]["heartbeat_bpm"] += 1
        result = audit_phenotype(ledger.snapshot(), observed, 3)
        self.assertEqual(result["status"], "mismatch")
        self.assertIn("parameters", result["mismatched_fields"])
        self.assertNotIn("event-a", json.dumps(result))

    def test_wrong_tick_is_detected(self):
        ledger = make_ledger()
        result = audit_phenotype(ledger.snapshot(), ledger.phenotype(2), 3)
        self.assertEqual(result["status"], "mismatch")
        self.assertIn("at_tick", result["mismatched_fields"])

    def test_missing_and_extra_fields_are_reported(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        observed.pop("source_classification")
        observed["unexpected"] = "value"
        result = audit_phenotype(ledger.snapshot(), observed, 3)
        self.assertEqual(result["mismatched_fields"],
                         ["source_classification", "unexpected"])

    def test_forged_ledger_revision_rejected(self):
        ledger = make_ledger()
        snap = ledger.snapshot()
        snap["revision"] = "0" * 64
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(snap, ledger.phenotype(3), 3)

    def test_tampered_ledger_event_rejected(self):
        ledger = make_ledger()
        snap = ledger.snapshot()
        snap["events"][0]["delta"] = 99
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(snap, ledger.phenotype(3), 3)

    def test_input_is_not_mutated(self):
        ledger = make_ledger()
        snap, phenotype = ledger.snapshot(), ledger.phenotype(3)
        before = copy.deepcopy((snap, phenotype))
        audit_phenotype(snap, phenotype, 3)
        self.assertEqual((snap, phenotype), before)

    def test_invalid_tick_types_rejected(self):
        ledger = make_ledger()
        for tick in (True, -1, 1.0, "3", 2**63):
            with self.subTest(tick=tick):
                with self.assertRaises(GenesisPhenotypeAuditError):
                    audit_phenotype(ledger.snapshot(), ledger.phenotype(3), tick)

    def test_non_object_snapshots_rejected(self):
        ledger = make_ledger()
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype([], ledger.phenotype(3), 3)
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(ledger.snapshot(), [], 3)

    def test_subclassed_containers_rejected(self):
        ledger = make_ledger()
        class EvilDict(dict):
            pass
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(EvilDict(ledger.snapshot()), ledger.phenotype(3), 3)

    def test_oversized_input_rejected(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        observed["padding"] = "x" * (1_048_576 + 1)
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(ledger.snapshot(), observed, 3)

    def test_aggregate_input_budget_rejected_before_serialization(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        observed["first"] = "a" * 600_000
        observed["second"] = "b" * 600_000
        with patch(
            "mojum.genesis_phenotype_audit.canonical",
            wraps=genesis_canonical,
        ) as serializer:
            with self.assertRaises(GenesisPhenotypeAuditError):
                audit_phenotype(ledger.snapshot(), observed, 3)
            # The bounded ledger is serialized once; the rejected projection
            # must fail its preflight before a second serialization call.
            self.assertEqual(serializer.call_count, 1)

    def test_aggregate_object_key_budget_rejected_before_serialization(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        observed["k" * 600_000] = 1
        observed["v" * 600_000] = 2
        with patch(
            "mojum.genesis_phenotype_audit.canonical",
            wraps=genesis_canonical,
        ) as serializer:
            with self.assertRaises(GenesisPhenotypeAuditError):
                audit_phenotype(ledger.snapshot(), observed, 3)
            # The bounded ledger is serialized once; the rejected projection
            # must fail its preflight before a second serialization call.
            self.assertEqual(serializer.call_count, 1)

    def test_non_finite_float_rejected_before_serialization(self):
        ledger = make_ledger()
        for value in (float("nan"), float("inf"), float("-inf")):
            observed = ledger.phenotype(3)
            observed["padding"] = value
            with self.subTest(value=value):
                with patch(
                    "mojum.genesis_phenotype_audit.canonical",
                    wraps=genesis_canonical,
                ) as serializer:
                    with self.assertRaises(GenesisPhenotypeAuditError):
                        audit_phenotype(ledger.snapshot(), observed, 3)
                    self.assertEqual(serializer.call_count, 1)

    def test_excessive_nesting_rejected(self):
        ledger = make_ledger()
        observed = ledger.phenotype(3)
        value = None
        for _ in range(40):
            value = [value]
        observed["padding"] = value
        with self.assertRaises(GenesisPhenotypeAuditError):
            audit_phenotype(ledger.snapshot(), observed, 3)

    def test_digest_is_deterministic(self):
        ledger = make_ledger()
        first = audit_phenotype(ledger.snapshot(), ledger.phenotype(3), 3)
        second = audit_phenotype(ledger.snapshot(), ledger.phenotype(3), 3)
        self.assertEqual(first, second)

    def test_cross_tick_projection_does_not_match(self):
        ledger = make_ledger()
        result = audit_phenotype(ledger.snapshot(), ledger.phenotype(0), 3)
        self.assertEqual(result["status"], "mismatch")
        self.assertIn("applied_acquisition_ids", result["mismatched_fields"])


if __name__ == "__main__":
    unittest.main()
