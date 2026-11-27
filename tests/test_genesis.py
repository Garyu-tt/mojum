import copy
import json
import unittest
from collections import UserDict
from threading import Barrier, Thread
from unittest.mock import patch
from pathlib import Path
from mojum.genesis import GenesisError, GenomeLedger, ACQUISITION_SCHEMA

SEED = json.loads((Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text())


def event(eid="a", tick=10, delta=7, recorded=200, gene="heartbeat_bpm"):
    return {
        "schema": ACQUISITION_SCHEMA, "event_id": eid,
        "genome_id": SEED["genome_id"], "gene": gene, "delta": delta,
        "valid_from_tick": tick, "observed_at_ns": 100,
        "recorded_at_ns": recorded, "source": "test:fixture",
        "evidence": "synthetic, no real learning",
    }


class GenesisContractTests(unittest.TestCase):
    def test_seed_and_phenotype(self):
        l = GenomeLedger(SEED)
        self.assertEqual(l.phenotype(0)["parameters"]["heartbeat_bpm"], 60)
        self.assertEqual(l.phenotype(0)["source_classification"], "synthetic_fixture_not_anatomy_or_neural_model")

    def test_inherited_unmodified_after_event(self):
        orig = copy.deepcopy(SEED)
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        self.assertEqual(SEED, orig)
        self.assertEqual(l.snapshot()["seed"]["inherited"], orig["inherited"])

    def test_acquired_change_valid_from_tick(self):
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        self.assertEqual(l.phenotype(9)["parameters"]["heartbeat_bpm"], 60)
        self.assertEqual(l.phenotype(10)["parameters"]["heartbeat_bpm"], 67)
        self.assertEqual(l.phenotype(11)["applied_acquisition_ids"], ["a"])

    def test_revision_changes_on_append(self):
        l = GenomeLedger(SEED)
        prev = l.revision
        after = l.append(event(), prev)
        self.assertNotEqual(prev, after)
        self.assertEqual(after, l.revision)

    def test_cas_rejects_stale_revision(self):
        l = GenomeLedger(SEED)
        old = l.revision
        l.append(event(), old)
        with self.assertRaisesRegex(GenesisError, "CAS failed"):
            l.append(event("b", recorded=300), old)
        self.assertEqual(len(l.snapshot()["events"]), 1)

    def test_event_duplicate_rejected(self):
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        with self.assertRaisesRegex(GenesisError, "Duplicate"):
            l.append(event(), l.revision)

    def test_out_of_bounds_effective_phenotype_rejected(self):
        l = GenomeLedger(SEED)
        with self.assertRaisesRegex(GenesisError, "exceeds"):
            l.append(event(delta=200), l.revision)
        self.assertEqual(l.phenotype(10)["parameters"]["heartbeat_bpm"], 60)

    def test_atomic_rejects_intermediate_out_of_bounds(self):
        l = GenomeLedger(SEED)
        l.append(event(delta=80), l.revision)
        with self.assertRaises(GenesisError):
            l.append(event("b", tick=5, delta=-111, recorded=300), l.revision)

    def test_wrong_genome_rejected(self):
        l = GenomeLedger(SEED)
        e = event()
        e["genome_id"] = "foreign"
        with self.assertRaises(GenesisError):
            l.append(e, l.revision)

    def test_unknown_gene_rejected(self):
        l = GenomeLedger(SEED)
        with self.assertRaises(GenesisError):
            l.append(event(gene="arbitrary_code"), l.revision)

    def test_invalid_gene_type_rejected_as_contract_error(self):
        l = GenomeLedger(SEED)
        x = event()
        x["gene"] = []
        with self.assertRaises(GenesisError):
            l.append(x, l.revision)

    def test_observed_after_recorded_rejected(self):
        l = GenomeLedger(SEED)
        with self.assertRaises(GenesisError):
            l.append(event(recorded=50), l.revision)

    def test_no_floating_nan_bool_or_out_of_range_seed(self):
        for bad in (True, float("nan"), 0, 181):
            seed = copy.deepcopy(SEED)
            seed["inherited"]["heartbeat_bpm"] = bad
            with self.assertRaises(GenesisError):
                GenomeLedger(seed)

    def test_no_extra_seed_keys(self):
        seed = copy.deepcopy(SEED)
        seed["code"] = "print('arbitrary')"
        with self.assertRaises(GenesisError):
            GenomeLedger(seed)

    def test_snapshot_restore_exact(self):
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        restored = GenomeLedger.from_snapshot(l.snapshot())
        self.assertEqual(restored.revision, l.revision)
        self.assertEqual(restored.phenotype(9), l.phenotype(9))
        self.assertEqual(restored.phenotype(10), l.phenotype(10))

    def test_tampered_snapshot_rejected(self):
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        snap = l.snapshot()
        snap["events"][0]["delta"] = 9
        with self.assertRaises(GenesisError):
            GenomeLedger.from_snapshot(snap)

    def test_caller_cannot_mutate_ledger_via_snapshot(self):
        l = GenomeLedger(SEED)
        l.append(event(), l.revision)
        snap = l.snapshot()
        snap["seed"]["inherited"]["heartbeat_bpm"] = 180
        snap["events"][0]["delta"] = 42
        self.assertEqual(l.phenotype(10)["parameters"]["heartbeat_bpm"], 67)

    def test_acquired_multiple_effective_timelines(self):
        l = GenomeLedger(SEED)
        l.append(event("future", tick=20, delta=3, recorded=200), l.revision)
        l.append(event("earlier", tick=10, delta=2, recorded=300), l.revision)
        self.assertEqual(l.phenotype(0)["parameters"]["heartbeat_bpm"], 60)
        self.assertEqual(l.phenotype(15)["parameters"]["heartbeat_bpm"], 62)
        self.assertEqual(l.phenotype(25)["parameters"]["heartbeat_bpm"], 65)
        self.assertEqual(l.phenotype(25)["applied_acquisition_ids"], ["future", "earlier"])

    def test_replay_deterministic(self):
        first = GenomeLedger(SEED)
        second = GenomeLedger(copy.deepcopy(SEED))
        for x in (event(), event("b", tick=16, delta=-2, recorded=300)):
            first.append(x, first.revision)
            second.append(copy.deepcopy(x), second.revision)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(first.phenotype(20), second.phenotype(20))

    def test_monotonic_recorded_ns_required(self):
        l = GenomeLedger(SEED)
        l.append(event(recorded=300), l.revision)
        with self.assertRaises(GenesisError):
            l.append(event("b", recorded=200), l.revision)

    def test_event_limit_is_bounded(self):
        l = GenomeLedger(SEED)
        l.MAX_EVENTS = 2
        for i in range(2):
            l.append(event(str(i), recorded=200 + i, delta=0), l.revision)
        with self.assertRaises(GenesisError):
            l.append(event("overflow", recorded=400), l.revision)


    def test_invalid_unicode_seed_fields_rejected(self):
        for field in ("genome_id", "revision"):
            for invalid in ("\ud800", "prefix\udfff"):
                with self.subTest(field=field, invalid=ascii(invalid)):
                    seed = copy.deepcopy(SEED)
                    seed[field] = invalid
                    with self.assertRaisesRegex(GenesisError, "valid UTF-8"):
                        GenomeLedger(seed)

    def test_invalid_unicode_acquisition_is_atomic(self):
        for field in ("event_id", "source", "evidence"):
            with self.subTest(field=field):
                ledger = GenomeLedger(SEED)
                before = ledger.revision
                invalid = event()
                invalid[field] = "prefix\ud800"
                with self.assertRaisesRegex(GenesisError, "valid UTF-8"):
                    ledger.append(invalid, before)
                self.assertEqual(ledger.revision, before)
                self.assertEqual(ledger.snapshot()["events"], [])

    def test_canonical_json_rejects_nested_surrogates_but_keeps_unicode(self):
        import hashlib
        from mojum.genesis import canonical, sha256

        with self.assertRaisesRegex(GenesisError, "canonical UTF-8 JSON"):
            canonical({"nested": ["valid", "\ud800"]})
        with self.assertRaisesRegex(GenesisError, "canonical UTF-8 JSON"):
            sha256({"\udfff": "invalid key"})
        valid = {"name": "Данте", "emoji": "🐈", "lesson": "音楽"}
        expected = json.dumps(valid, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(canonical(valid), expected)
        self.assertEqual(sha256(valid), hashlib.sha256(expected.encode("utf-8")).hexdigest())

    def test_concurrent_cas_has_one_winner(self):
        ledger = GenomeLedger(SEED)
        revision = ledger.revision
        start = Barrier(3)
        outcomes = []
        def writer(eid):
            start.wait(3)
            try:
                ledger.append(event(eid), revision)
                outcomes.append("committed")
            except GenesisError as exc:
                outcomes.append(str(exc))
        threads = [Thread(target=writer,args=(eid,)) for eid in ("a","b")]
        for thread in threads: thread.start()
        start.wait(3)
        for thread in threads: thread.join(4)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(outcomes.count("committed"),1)
        self.assertEqual(sum("CAS failed" in s for s in outcomes),1)
        snap=ledger.snapshot()
        self.assertEqual(len(snap["events"]),1)
        self.assertEqual(GenomeLedger.from_snapshot(snap).snapshot(),snap)
        self.assertEqual(ledger.phenotype(10)["ledger_revision"],snap["revision"])

    def test_failed_revision_calculation_preserves_all_state(self):
        ledger = GenomeLedger(SEED)
        before = ledger.snapshot()
        for failure in (MemoryError("allocation"), KeyboardInterrupt()):
            with patch("mojum.genesis.sha256",side_effect=failure):
                with self.assertRaises(type(failure)):
                    ledger.append(event(),before["revision"])
            self.assertEqual(ledger.snapshot(),before)
        ledger.append(event(),before["revision"])
        with self.assertRaisesRegex(GenesisError,"Duplicate"):
            ledger.append(event(),ledger.revision)

    def test_nested_mapping_is_normalized(self):
        seed=copy.deepcopy(SEED)
        seed["inherited"]=UserDict(seed["inherited"])
        ledger=GenomeLedger(seed)
        self.assertEqual(ledger.snapshot(),GenomeLedger(SEED).snapshot())
        self.assertIs(type(ledger.snapshot()["seed"]["inherited"]),dict)
        seed["inherited"]["heartbeat_bpm"]=120
        self.assertEqual(ledger.phenotype(0)["parameters"]["heartbeat_bpm"],60)


if __name__ == "__main__":
    unittest.main()
