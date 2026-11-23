import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.genesis import GenomeLedger, ACQUISITION_SCHEMA, GenesisError
from mojum.genesis_timeline import audit_timeline, TIMELINE_SCHEMA

SEED = json.loads((Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text())


def event(event_id, tick, delta, recorded):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": event_id,
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": delta,
        "valid_from_tick": tick,
        "observed_at_ns": 100,
        "recorded_at_ns": recorded,
        "source": "test:synthetic",
        "evidence": "PUBLIC_SYNTHETIC fixture only",
    }


def populated():
    ledger = GenomeLedger(SEED)
    ledger.append(event("late", 20, 3, 200), ledger.revision)
    ledger.append(event("retroactive", 10, 2, 300), ledger.revision)
    return ledger


class GenesisTimelineTests(unittest.TestCase):
    def test_reports_tick_zero_and_each_distinct_boundary(self):
        report = audit_timeline(populated().snapshot())
        self.assertEqual(report["schema"], TIMELINE_SCHEMA)
        self.assertEqual([x["tick"] for x in report["boundaries"]], [0, 10, 20])
        self.assertEqual([x["parameters"]["heartbeat_bpm"] for x in report["boundaries"]], [60, 62, 65])

    def test_applied_ids_follow_ledger_append_order(self):
        report = audit_timeline(populated().snapshot())
        self.assertEqual(report["boundaries"][1]["applied_acquisition_ids"], ["retroactive"])
        self.assertEqual(report["boundaries"][2]["applied_acquisition_ids"], ["late", "retroactive"])

    def test_deterministic_report_and_digest(self):
        snap = populated().snapshot()
        self.assertEqual(audit_timeline(snap), audit_timeline(copy.deepcopy(snap)))

    def test_input_and_return_values_are_detached(self):
        snap = populated().snapshot()
        before = copy.deepcopy(snap)
        report = audit_timeline(snap)
        report["boundaries"][0]["parameters"]["heartbeat_bpm"] = -1
        report["boundaries"].clear()
        self.assertEqual(snap, before)
        self.assertEqual(len(audit_timeline(snap)["boundaries"]), 3)

    def test_empty_ledger_has_baseline_only(self):
        report = audit_timeline(GenomeLedger(SEED).snapshot())
        self.assertEqual(len(report["boundaries"]), 1)
        self.assertEqual(report["boundaries"][0]["tick"], 0)

    def test_report_digest_covers_boundaries(self):
        report = audit_timeline(populated().snapshot())
        self.assertEqual(len(report["report_sha256"]), 64)
        self.assertTrue(all(len(x["phenotype_sha256"]) == 64 for x in report["boundaries"]))

    def test_corrupted_snapshot_rejected(self):
        snap = populated().snapshot()
        snap["events"][0]["delta"] += 1
        with self.assertRaises(GenesisError):
            audit_timeline(snap)

    def test_stale_or_foreign_snapshot_rejected(self):
        with self.assertRaises(GenesisError):
            audit_timeline({"schema": "wrong"})

    def test_hostile_container_subclasses_rejected(self):
        class HostileDict(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("custom mapping code must not execute")

        class HostileList(list):
            def __iter__(self):
                raise AssertionError("custom sequence code must not execute")

        snap = populated().snapshot()
        with self.assertRaises(GenesisError):
            audit_timeline(HostileDict(snap))
        snap = populated().snapshot()
        snap["events"] = HostileList(snap["events"])
        with self.assertRaises(GenesisError):
            audit_timeline(snap)

    def test_oversized_event_list_rejected_before_content_scan(self):
        snap = populated().snapshot()
        snap["events"] = [{}] * (GenomeLedger.MAX_EVENTS + 1)
        with patch("mojum.genesis_timeline.any", side_effect=AssertionError("oversized list was scanned"), create=True):
            with self.assertRaisesRegex(GenesisError, "event limit"):
                audit_timeline(snap)

    def test_boundary_limit_rejected_before_report(self):
        with self.assertRaisesRegex(GenesisError, "boundary limit"):
            audit_timeline(populated().snapshot(), max_boundaries=2)

    def test_max_boundaries_exact_native_integer(self):
        for value in (True, 1.0, "3", 0, 1026):
            with self.subTest(value=value):
                with self.assertRaises(GenesisError):
                    audit_timeline(GenomeLedger(SEED).snapshot(), max_boundaries=value)

    def test_duplicate_valid_from_ticks_collapse(self):
        ledger = GenomeLedger(SEED)
        ledger.append(event("a", 10, 1, 200), ledger.revision)
        ledger.append(event("b", 10, 1, 300), ledger.revision)
        report = audit_timeline(ledger.snapshot())
        self.assertEqual([x["tick"] for x in report["boundaries"]], [0, 10])
        self.assertEqual(report["boundaries"][1]["parameters"]["heartbeat_bpm"], 62)

    def test_snapshot_remains_replayable(self):
        ledger = populated()
        before = ledger.revision
        report = audit_timeline(ledger.snapshot())
        self.assertEqual(ledger.revision, before)
        restored = GenomeLedger.from_snapshot(ledger.snapshot())
        self.assertEqual(report["ledger_revision"], restored.revision)


if __name__ == "__main__":
    unittest.main()
