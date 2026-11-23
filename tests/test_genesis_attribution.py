import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from mojum.genesis import ACQUISITION_SCHEMA, GenomeLedger
from mojum.genesis_attribution import ATTRIBUTION_SCHEMA, AttributionError, audit_attribution

SEED = json.loads((Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text())


def event(eid, gene, delta, tick, recorded):
    return {
        "schema": ACQUISITION_SCHEMA, "event_id": eid,
        "genome_id": SEED["genome_id"], "gene": gene, "delta": delta,
        "valid_from_tick": tick, "observed_at_ns": recorded - 10,
        "recorded_at_ns": recorded, "source": "test:public-synthetic",
        "evidence": "fixture only",
    }


def sample_snapshot():
    ledger = GenomeLedger(SEED)
    ledger.append(event("pulse-a", "heartbeat_bpm", 7, 10, 100), ledger.revision)
    ledger.append(event("scale-b", "body_scale_milli", 50, 12, 200), ledger.revision)
    ledger.append(event("pulse-future", "heartbeat_bpm", -2, 30, 300), ledger.revision)
    return ledger.snapshot()


class GenesisAttributionTests(unittest.TestCase):
    def test_decomposes_effective_and_future_events(self):
        report = audit_attribution(sample_snapshot(), 15)
        self.assertEqual(report["schema"], ATTRIBUTION_SCHEMA)
        self.assertEqual(report["inherited_parameters"]["heartbeat_bpm"], 60)
        self.assertEqual(report["effective_acquired_delta_by_gene"]["heartbeat_bpm"], 7)
        self.assertEqual(report["effective_acquired_delta_by_gene"]["body_scale_milli"], 50)
        self.assertEqual(report["effective_parameters"]["heartbeat_bpm"], 67)
        self.assertEqual(report["effective_parameters"]["body_scale_milli"], 1050)
        self.assertEqual(report["effective_acquisition_count_by_gene"]["heartbeat_bpm"], 1)
        self.assertEqual(report["effective_acquisition_count"], 2)
        self.assertEqual(report["future_acquisition_count"], 1)
        self.assertTrue(report["reconciled"])

    def test_tick_before_any_acquisition_is_inherited_only(self):
        report = audit_attribution(sample_snapshot(), 0)
        self.assertEqual(report["effective_acquisition_count"], 0)
        self.assertEqual(report["future_acquisition_count"], 3)
        self.assertEqual(report["effective_parameters"], report["inherited_parameters"])
        self.assertEqual(set(report["effective_acquired_delta_by_gene"].values()), {0})

    def test_event_becomes_effective_at_exact_tick(self):
        snap = sample_snapshot()
        self.assertEqual(audit_attribution(snap, 9)["effective_acquisition_count"], 0)
        self.assertEqual(audit_attribution(snap, 10)["effective_acquisition_count"], 1)

    def test_all_events_effective_at_later_tick(self):
        report = audit_attribution(sample_snapshot(), 30)
        self.assertEqual(report["effective_acquisition_count"], 3)
        self.assertEqual(report["future_acquisition_count"], 0)
        self.assertEqual(report["effective_parameters"]["heartbeat_bpm"], 65)

    def test_report_is_deterministic_and_tick_bound(self):
        snap = sample_snapshot()
        self.assertEqual(audit_attribution(snap, 15), audit_attribution(snap, 15))
        self.assertNotEqual(audit_attribution(snap, 10)["report_sha256"],
                            audit_attribution(snap, 11)["report_sha256"])

    def test_input_is_not_mutated_and_output_is_redacted(self):
        snap = sample_snapshot()
        before = copy.deepcopy(snap)
        report = audit_attribution(snap, 15)
        self.assertEqual(snap, before)
        rendered = json.dumps(report, sort_keys=True)
        for secret in ("pulse-a", "pulse-future", "test:public-synthetic", "fixture only"):
            self.assertNotIn(secret, rendered)

    def test_empty_ledger_is_supported(self):
        report = audit_attribution(GenomeLedger(SEED).snapshot(), 0)
        self.assertEqual(report["effective_parameters"], SEED["inherited"])
        self.assertEqual(report["effective_acquisition_count"], 0)
        self.assertEqual(report["future_acquisition_count"], 0)

    def test_same_gene_deltas_sum(self):
        ledger = GenomeLedger(SEED)
        ledger.append(event("a", "heartbeat_bpm", 5, 10, 100), ledger.revision)
        ledger.append(event("b", "heartbeat_bpm", -3, 12, 200), ledger.revision)
        report = audit_attribution(ledger.snapshot(), 20)
        self.assertEqual(report["effective_acquired_delta_by_gene"]["heartbeat_bpm"], 2)
        self.assertEqual(report["effective_parameters"]["heartbeat_bpm"], 62)
        self.assertEqual(report["effective_acquisition_count_by_gene"]["heartbeat_bpm"], 2)

    def test_invalid_ticks_rejected(self):
        for tick in (-1, True, 1.0, 2**63):
            with self.subTest(tick=tick), self.assertRaises(AttributionError):
                audit_attribution(sample_snapshot(), tick)

    def test_non_object_snapshot_rejected(self):
        for value in (None, [], "snapshot"):
            with self.subTest(value=value), self.assertRaises(AttributionError):
                audit_attribution(value, 0)

    def test_tampered_revision_and_unknown_fields_rejected(self):
        snap = sample_snapshot()
        snap["revision"] = "0" * 64
        with self.assertRaises(AttributionError):
            audit_attribution(snap, 15)
        snap = sample_snapshot()
        snap["unexpected"] = "value"
        with self.assertRaises(AttributionError):
            audit_attribution(snap, 15)

    def test_non_finite_and_non_json_values_rejected(self):
        for bad in (float("nan"), float("inf"), object()):
            snap = sample_snapshot()
            snap["bad"] = bad
            with self.subTest(bad=type(bad).__name__), self.assertRaises(AttributionError):
                audit_attribution(snap, 0)

    def test_lone_surrogate_rejected(self):
        snap = sample_snapshot()
        snap["seed"]["genome_id"] = "\ud800"
        with self.assertRaises(AttributionError):
            audit_attribution(snap, 0)

    def test_event_count_limit_rejected(self):
        snap = sample_snapshot()
        # The canonical reader rejects the count before replaying event data.
        snap["events"] = [snap["events"][0]] * 1025
        snap["revision"] = "0" * 64
        with self.assertRaisesRegex(AttributionError, "invalid GENESIS ledger snapshot"):
            audit_attribution(snap, 0)

    def test_snapshot_byte_limit_rejected(self):
        snap = sample_snapshot()
        snap["padding"] = "x" * 270_000
        with self.assertRaises(AttributionError):
            audit_attribution(snap, 0)

    def test_aggregate_text_budget_rejected_before_serialization(self):
        snap = sample_snapshot()
        snap["padding"] = ["x" * MAX_TEXT_LENGTH for _ in range(40)]
        with patch(
            "mojum.genesis_attribution.json.dumps",
            side_effect=AssertionError("json.dumps must not run"),
        ):
            with self.assertRaises(AttributionError):
                audit_attribution(snap, 15)

    def test_aggregate_key_budget_rejected_before_serialization(self):
        snap = sample_snapshot()
        snap.update({("k" * 8_000) + str(i): i for i in range(40)})
        with patch(
            "mojum.genesis_attribution.json.dumps",
            side_effect=AssertionError("json.dumps must not run"),
        ):
            with self.assertRaises(AttributionError):
                audit_attribution(snap, 15)

    def test_nested_snapshot_depth_is_bounded(self):
        snap = sample_snapshot()
        value = "leaf"
        for _ in range(40):
            value = [value]
        snap["padding"] = value
        with self.assertRaises(AttributionError):
            audit_attribution(snap, 0)


if __name__ == "__main__":
    unittest.main()
