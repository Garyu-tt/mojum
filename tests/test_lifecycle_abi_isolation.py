"""Regression: two GENESIS lifecycle experiments must not share an incompatible ABI."""
import unittest

from mojum.lifecycle import (
    SCHEMA as CANONICAL_SCHEMA,
    DevelopmentClock,
    LifecycleError as CanonicalLifecycleError,
)
from mojum.lifecycle_event_ledger import (
    PROFILE_SCHEMA,
    SNAPSHOT_SCHEMA,
    Lifecycle,
    LifecycleError as LedgerLifecycleError,
)


class LifecycleABIIsolationTests(unittest.TestCase):
    @staticmethod
    def ledger():
        return Lifecycle({
            "schema": PROFILE_SCHEMA,
            "organism_id": "isolated-ledger-fixture",
            "senescence_enabled": False,
            "regions": {
                "cortex": {
                    "initial_mature": 5, "initial_immature": 4,
                    "newborn_cap": 3, "birth_per_evidence": 0,
                    "mature_per_evidence": 2, "synapses_initial": 10,
                    "synapses_cap": 40, "synapses_per_learning": 1,
                    "prune_per_evidence": 0, "prune_cap": 0,
                },
            },
        })

    def test_canonical_public_contract_stays_available(self):
        self.assertEqual(CANONICAL_SCHEMA, "mojum.lifecycle.synthetic-populations/1")
        canonical = DevelopmentClock("canonical-fixture", "a" * 64)
        snapshot = canonical.snapshot()
        self.assertEqual(snapshot["schema"], CANONICAL_SCHEMA)
        self.assertEqual(DevelopmentClock.from_snapshot(snapshot).snapshot(), snapshot)

    def test_birth_disabled_region_can_reach_maturity(self):
        ledger = self.ledger()
        ledger.experience("cortex", development_units=2,
                          expected_revision=ledger.revision)
        region = ledger.region("cortex")
        self.assertEqual(region["immature_neurons"], 0)
        self.assertTrue(region["structurally_mature"])

    def test_ledger_replay_independent_from_canonical(self):
        ledger = self.ledger()
        ledger.advance(1000, expected_revision=ledger.revision)
        ledger.experience("cortex", learning_units=3,
                          expected_revision=ledger.revision)
        snap = ledger.snapshot()
        self.assertEqual(snap["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(Lifecycle.from_snapshot(snap).snapshot(), snap)

    def test_cross_api_snapshots_rejected_not_migrated(self):
        canonical = DevelopmentClock("canonical-fixture", "a" * 64)
        ledger = self.ledger()
        with self.assertRaises(LedgerLifecycleError):
            Lifecycle.from_snapshot(canonical.snapshot())
        with self.assertRaises(CanonicalLifecycleError):
            DevelopmentClock.from_snapshot(ledger.snapshot())


if __name__ == "__main__":
    unittest.main()
