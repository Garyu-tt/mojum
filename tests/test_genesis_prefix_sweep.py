"""Effective-prefix cost and exact existing GENESIS/Aquarium semantics."""
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.genesis import ACQUISITION_SCHEMA, GENES, GenesisError, GenomeLedger, sha256

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text())
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text())


def acquisition(index, tick, delta=0, gene="heartbeat_bpm"):
    return {
        "schema": ACQUISITION_SCHEMA, "event_id": f"acquisition-{index}",
        "genome_id": SEED["genome_id"], "gene": gene, "delta": delta,
        "valid_from_tick": tick, "observed_at_ns": 0, "recorded_at_ns": 0,
        "source": "PUBLIC_SYNTHETIC", "evidence": "effective-prefix regression",
    }


def reference_failure(events):
    """Independent brute-force projection of the existing boundary contract."""
    for tick in sorted({0, *(e["valid_from_tick"] for e in events)}):
        for gene, (low, high) in GENES.items():
            value = SEED["inherited"][gene] + sum(
                e["delta"] for e in events
                if e["gene"] == gene and e["valid_from_tick"] <= tick
            )
            if not low <= value <= high:
                return f"Acquired genome exceeds {gene} bounds at tick {tick}"
    return None


class GenesisPrefixSweepTests(unittest.TestCase):
    def test_effective_validation_has_linear_event_reads(self):
        # Count actual event-field work instead of asserting machine-dependent
        # latency. The internal helper receives already validated events.
        class CountedEvent(dict):
            reads = 0

            def __getitem__(self, key):
                type(self).reads += 1
                return super().__getitem__(key)

        for size in (64, 256, GenomeLedger.MAX_EVENTS):
            with self.subTest(events=size):
                events = [CountedEvent(acquisition(i, i + 1)) for i in range(size)]
                CountedEvent.reads = 0
                GenomeLedger(SEED)._check_all_effective_prefixes(events)
                self.assertLessEqual(CountedEvent.reads, 8 * size)

    def test_projection_matches_brute_force_at_every_boundary(self):
        rng = random.Random(1486)
        ledger = GenomeLedger(SEED)
        genes = tuple(GENES)
        for case in range(180):
            events = [acquisition(
                i, rng.choice((0, 1, 2, 10, 2**63 - 1)),
                rng.choice((-2000, -121, -30, -1, 0, 1, 30, 121, 2000)),
                rng.choice(genes),
            ) for i in range(rng.randrange(25))]
            before = copy.deepcopy(events)
            with self.subTest(case=case):
                expected = reference_failure(events)
                if expected is None:
                    ledger._check_all_effective_prefixes(events)
                else:
                    with self.assertRaises(GenesisError) as caught:
                        ledger._check_all_effective_prefixes(events)
                    self.assertEqual(str(caught.exception), expected)
                self.assertEqual(events, before)

    def test_simultaneous_deltas_are_checked_as_a_group(self):
        for early_tick in (0, 5):
            for deltas, expected in (((120, -100, 50), (110, 130)),
                                     ((-30, 100, -20), (40, 110))):
                with self.subTest(early_tick=early_tick, deltas=deltas):
                    ledger = GenomeLedger(SEED)
                    events = [acquisition(0, 10, deltas[0]),
                              acquisition(1, 10, deltas[1]),
                              acquisition(2, early_tick, deltas[2])]
                    for event in events:
                        ledger.append(event, ledger.revision)
                    snapshot = ledger.snapshot()
                    self.assertEqual(snapshot["events"], events)
                    self.assertEqual(ledger.phenotype(early_tick)["parameters"]["heartbeat_bpm"], expected[0])
                    final = ledger.phenotype(10)
                    self.assertEqual(final["parameters"]["heartbeat_bpm"], expected[1])
                    self.assertEqual(final["applied_acquisition_ids"], [e["event_id"] for e in events])
                    self.assertEqual(GenomeLedger.from_snapshot(snapshot).snapshot(), snapshot)

    def test_all_genes_and_maximum_effective_tick_preserve_exact_revision(self):
        events = [acquisition(0, 2**63 - 1, 7),
                  acquisition(1, 0, -30),
                  acquisition(2, 4, 10, "body_scale_milli"),
                  acquisition(3, 2, -10, "sensory_gain_milli")]
        ledger = GenomeLedger(SEED)
        for event in events:
            ledger.append(event, ledger.revision)
        expected = {"schema": "mojum.genesis.ledger/1", "seed": SEED,
                    "events": events, "revision": sha256({"seed": SEED, "events": events})}
        self.assertEqual(ledger.snapshot(), expected)
        restored = GenomeLedger.from_snapshot(expected)
        self.assertEqual(restored.snapshot(), expected)
        self.assertEqual(restored.phenotype(2**63 - 1), ledger.phenotype(2**63 - 1))

    def test_earliest_invalid_boundary_rejection_is_atomic(self):
        ledger = GenomeLedger(SEED)
        ledger.append(acquisition(0, 10, 100), ledger.revision)
        before = ledger.snapshot()
        with self.assertRaisesRegex(GenesisError, "heartbeat_bpm bounds at tick 5$"):
            ledger.append(acquisition(1, 5, -31), ledger.revision)
        self.assertEqual(ledger.snapshot(), before)

    def test_snapshot_still_rejects_invalid_recorded_prefix(self):
        # The final projection cancels to the valid seed, but the first
        # recorded acquisition could never have passed ordinary append.
        events = [acquisition(0, 10, 200), acquisition(1, 10, -200)]
        snapshot = {"schema": "mojum.genesis.ledger/1", "seed": copy.deepcopy(SEED),
                    "events": events, "revision": sha256({"seed": SEED, "events": events})}
        before = copy.deepcopy(snapshot)
        with self.assertRaisesRegex(GenesisError, "heartbeat_bpm bounds at tick 10$"):
            GenomeLedger.from_snapshot(snapshot)
        self.assertEqual(snapshot, before)

    def test_maximum_valid_snapshot_preserves_order_and_budget(self):
        events = [acquisition(i, GenomeLedger.MAX_EVENTS - i)
                  for i in range(GenomeLedger.MAX_EVENTS)]
        snapshot = {"schema": "mojum.genesis.ledger/1", "seed": copy.deepcopy(SEED),
                    "events": events, "revision": sha256({"seed": SEED, "events": events})}
        restored = GenomeLedger.from_snapshot(snapshot)
        self.assertEqual(restored.snapshot(), snapshot)
        with self.assertRaisesRegex(GenesisError, "event count limit"):
            restored.append(acquisition(GenomeLedger.MAX_EVENTS, 0), restored.revision)
        self.assertEqual(restored.snapshot(), snapshot)

    def test_real_aquarium_acquisition_resume_and_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.sqlite3"
            with Aquarium(path, SEED, GRAPH) as aquarium:
                for i in range(32):
                    aquarium.acquire(acquisition(i, 32 - i))
                aquarium.acquire(acquisition(32, 1, 15))
                before = aquarium.checkpoint()
                genome = aquarium.genome.snapshot()
                count = aquarium.stored_events
            with Aquarium(path, SEED, GRAPH, read_only=True,
                          external_expected_head=before["journal_head"]) as restored:
                self.assertEqual(restored.checkpoint(), before)
                self.assertEqual(restored.genome.snapshot(), genome)
                self.assertEqual(restored.stored_events, count)
            with Aquarium(path, SEED, GRAPH,
                          external_expected_head=before["journal_head"]) as resumed:
                frames = [resumed.step(), resumed.step()]
                self.assertEqual(resumed.genome.phenotype(0)["parameters"]["heartbeat_bpm"], 60)
                self.assertEqual(resumed.genome.phenotype(1)["parameters"]["heartbeat_bpm"], 75)
                after = resumed.checkpoint()
                self.assertEqual(after["tick"], 2)
                self.assertEqual(resumed.stored_events, count + 2)
                self.assertEqual([f["tick"] for f in frames], [0, 1])
            with Aquarium(path, SEED, GRAPH, read_only=True,
                          external_expected_head=after["journal_head"]) as restored:
                self.assertEqual(restored.checkpoint(), after)
                self.assertEqual(restored.genome.snapshot(), genome)


if __name__ == "__main__":
    unittest.main()
