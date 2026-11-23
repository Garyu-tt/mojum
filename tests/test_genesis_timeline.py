"""Independent synthetic GENESIS timeline boundary and adversarial tests."""
import copy
import json
import unittest

from mojum.genesis import ACQUISITION_SCHEMA, GENOTYPE_SCHEMA, GenesisError, GenomeLedger, sha256
from mojum.genesis_timeline import SCHEMA, attest_timeline


SEED = {
    "schema": GENOTYPE_SCHEMA,
    "genome_id": "fixture:timeline",
    "revision": "synthetic-r01",
    "inherited": {
        "heartbeat_bpm": 60, "body_scale_milli": 1000,
        "sensory_gain_milli": 1000,
    },
}


def acquisition(eid, tick, gene, delta, recorded):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": eid,
        "genome_id": SEED["genome_id"],
        "gene": gene,
        "delta": delta,
        "valid_from_tick": tick,
        "observed_at_ns": 100,
        "recorded_at_ns": recorded,
        "source": "test:private-description-that-must-not-leak",
        "evidence": "synthetic:evidence-that-must-not-leak",
    }


class GenesisTimelineTests(unittest.TestCase):
    def setUp(self):
        self.ledger = GenomeLedger(SEED)
        # Recorded order intentionally differs from activation order.
        self.ledger.append(acquisition("late", 20, "heartbeat_bpm", 3, 200), self.ledger.revision)
        self.ledger.append(acquisition("early", 5, "sensory_gain_milli", 100, 300), self.ledger.revision)
        self.ledger.append(acquisition("shared", 20, "heartbeat_bpm", -1, 400), self.ledger.revision)

    def report(self, start=0, end=30):
        return attest_timeline(self.ledger.snapshot(), start_tick=start,
                               through_tick=end, public_synthetic=True)

    def test_boundaries_and_projections_are_effective_not_recorded_order(self):
        r = self.report()
        self.assertEqual(r["schema"], SCHEMA)
        self.assertEqual([b["tick"] for b in r["boundaries"]], [0, 5, 20, 30])
        self.assertEqual([b["parameters"]["heartbeat_bpm"] for b in r["boundaries"]],
                         [60, 60, 62, 62])
        self.assertEqual([b["parameters"]["sensory_gain_milli"] for b in r["boundaries"]],
                         [1000, 1100, 1100, 1100])
        self.assertEqual([e["event_id"] for e in r["boundaries"][2]["newly_effective"]],
                         ["late", "shared"])
        self.assertEqual(r["boundaries"][2]["delta_since_previous"]["heartbeat_bpm"], 2)
        self.assertEqual(r["boundaries"][3]["newly_effective"], [])

    def test_window_start_includes_prior_events_and_end_closes_report(self):
        r = self.report(10, 20)
        self.assertEqual([b["tick"] for b in r["boundaries"]], [10, 20])
        self.assertEqual([e["event_id"] for e in r["boundaries"][0]["newly_effective"]], ["early"])
        self.assertEqual(r["boundaries"][0]["delta_since_previous"]["sensory_gain_milli"], 100)
        self.assertEqual([e["event_id"] for e in r["boundaries"][1]["newly_effective"]],
                         ["late", "shared"])

    def test_identical_start_end_emits_one_boundary(self):
        r = self.report(20, 20)
        self.assertEqual(len(r["boundaries"]), 1)
        self.assertEqual(r["boundaries"][0]["effective_acquisition_count"], 3)

    def test_empty_genome_has_two_boundaries_with_zero_deltas(self):
        r = attest_timeline(GenomeLedger(SEED).snapshot(), start_tick=0,
                            through_tick=100, public_synthetic=True)
        self.assertEqual(len(r["boundaries"]), 2)
        self.assertEqual(r["boundaries"][1]["effective_acquisition_count"], 0)
        self.assertTrue(all(v == 0 for v in r["boundaries"][1]["delta_since_previous"].values()))

    def test_content_hash_and_replay_deterministic(self):
        r = self.report()
        expected = copy.deepcopy(r)
        digest = expected.pop("sha256")
        self.assertEqual(digest, sha256(expected))
        self.assertEqual(json.dumps(r, sort_keys=True), json.dumps(self.report(), sort_keys=True))
        self.assertEqual(self.ledger.snapshot(), GenomeLedger.from_snapshot(self.ledger.snapshot()).snapshot())

    def test_snapshot_and_ledger_remain_unmodified(self):
        snap = self.ledger.snapshot()
        before = copy.deepcopy(snap)
        original_revision = self.ledger.revision
        out = attest_timeline(snap, start_tick=0, through_tick=30, public_synthetic=True)
        out["boundaries"][1]["parameters"]["heartbeat_bpm"] = 999
        self.assertEqual(snap, before)
        self.assertEqual(self.ledger.revision, original_revision)

    def test_no_raw_evidence_or_source_leaks(self):
        raw = json.dumps(self.report())
        self.assertNotIn("private-description", raw)
        self.assertNotIn("evidence-that-must-not-leak", raw)
        self.assertIn("event_sha256", raw)
        self.assertIn("observed_at_ns", raw)

    def test_rejects_tampered_snapshot_and_unknown_event(self):
        snap = self.ledger.snapshot()
        snap["events"][1]["delta"] = 999
        with self.assertRaises(GenesisError):
            attest_timeline(snap, start_tick=0, through_tick=30, public_synthetic=True)

    def test_approval_guard_rejects_default_and_non_true(self):
        for bad in (False, 1, "true", None, [], {}):
            with self.subTest(value=bad), self.assertRaisesRegex(GenesisError, "public_synthetic"):
                attest_timeline(self.ledger.snapshot(), start_tick=0,
                                through_tick=30, public_synthetic=bad)
        with self.assertRaises(GenesisError):
            attest_timeline(self.ledger.snapshot(), start_tick=0, through_tick=30)

    def test_fail_closed_window_types_and_limits(self):
        for bad in (True, -1, 1.0, "1", 2**63, None):
            with self.subTest(value=bad), self.assertRaises(GenesisError):
                attest_timeline(self.ledger.snapshot(), start_tick=bad,
                                through_tick=30, public_synthetic=True)
        with self.assertRaisesRegex(GenesisError, "precedes"):
            self.report(30, 10)
        with self.assertRaises(GenesisError):
            attest_timeline([], start_tick=0, through_tick=1, public_synthetic=True)

    def test_boundary_budget_rejects_without_truncation(self):
        ledger = GenomeLedger(SEED)
        for i in range(260):
            ledger.append(acquisition(str(i), i+1, "heartbeat_bpm", 0, 200+i), ledger.revision)
        with self.assertRaisesRegex(GenesisError, "boundary budget"):
            attest_timeline(ledger.snapshot(), start_tick=0,
                            through_tick=300, public_synthetic=True)

    def test_dense_same_tick_events_count_as_one_boundary(self):
        ledger = GenomeLedger(SEED)
        for i in range(256):
            ledger.append(acquisition(str(i), 1, "heartbeat_bpm", 0, 200+i), ledger.revision)
        r = attest_timeline(ledger.snapshot(), start_tick=0,
                            through_tick=5, public_synthetic=True)
        self.assertEqual([x["tick"] for x in r["boundaries"]], [0, 1, 5])
        self.assertEqual(len(r["boundaries"][1]["newly_effective"]), 256)


    def test_hostile_revision_and_schema_equality_never_executes(self):
        class Impostor:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class ExplodingComparison:
            def __eq__(self, other):
                raise AssertionError("attacker comparison executed")

            def __ne__(self, other):
                raise AssertionError("attacker comparison executed")

        class StringSubclass(str):
            pass

        original = self.ledger.snapshot()
        for key in ("schema", "revision"):
            for malicious in (Impostor(), ExplodingComparison(),
                              StringSubclass(original[key])):
                with self.subTest(key=key, malicious=type(malicious).__name__):
                    forged = copy.deepcopy(original)
                    forged[key] = malicious
                    with self.assertRaises(GenesisError):
                        attest_timeline(forged, start_tick=0, through_tick=30,
                                        public_synthetic=True)

    def test_nested_ledger_native_text_guards_precede_comparisons(self):
        class Impostor:
            def __ne__(self, other):
                return False

        for location, field in (("seed", "schema"), ("seed", "genome_id"),
                                ("event", "schema"), ("event", "gene"),
                                ("event", "genome_id")):
            with self.subTest(location=location, field=field):
                forged = self.ledger.snapshot()
                if location == "seed":
                    forged["seed"][field] = Impostor()
                else:
                    forged["events"][0][field] = Impostor()
                with self.assertRaises(GenesisError):
                    attest_timeline(forged, start_tick=0, through_tick=30,
                                    public_synthetic=True)

    def test_hostile_mapping_keys_fail_before_hash_and_equality(self):
        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile key comparison executed")

        for level in ("root", "seed", "inherited", "event"):
            with self.subTest(level=level):
                forged = self.ledger.snapshot()
                if level == "root":
                    forged = {HostileKey(k): v for k, v in forged.items()}
                elif level == "seed":
                    forged["seed"] = {HostileKey(k): v
                                      for k, v in forged["seed"].items()}
                elif level == "inherited":
                    inherited = forged["seed"]["inherited"]
                    forged["seed"]["inherited"] = {
                        HostileKey(k): v for k, v in inherited.items()
                    }
                else:
                    forged["events"][0] = {
                        HostileKey(k): v for k, v in forged["events"][0].items()
                    }
                with self.assertRaises(GenesisError):
                    attest_timeline(forged, start_tick=0, through_tick=30,
                                    public_synthetic=True)


if __name__ == "__main__":
    unittest.main()
