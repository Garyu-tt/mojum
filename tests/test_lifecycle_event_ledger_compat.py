"""Cross-contract regression: experimental event ledger never overrides canonical API."""
import unittest

from mojum.lifecycle import DevelopmentClock, LifecycleError as CanonicalError, SCHEMA as CANONICAL_SCHEMA
from mojum.lifecycle_event_ledger import (
    Lifecycle, LifecycleError as EventError, PROFILE_SCHEMA, SNAPSHOT_SCHEMA,
)

REGION = {
    "initial_mature": 5, "initial_immature": 8,
    "newborn_cap": 4, "birth_per_evidence": 0,
    "mature_per_evidence": 2, "synapses_initial": 10,
    "synapses_cap": 30, "synapses_per_learning": 2,
    "prune_per_evidence": 1, "prune_cap": 2,
}
PROFILE = {
    "schema": PROFILE_SCHEMA, "organism_id": "compatibility-fixture",
    "senescence_enabled": False, "regions": {"cortex": REGION},
}


class EventLedgerSidecarCompatibilityTests(unittest.TestCase):
    def test_canonical_api_and_event_sidecar_coexist_without_mutation(self):
        canonical = DevelopmentClock("compatibility-fixture", "a" * 64)
        canonical_before = canonical.snapshot()
        sidecar = Lifecycle(PROFILE)
        sidecar.advance(100, expected_revision=sidecar.revision)
        sidecar.experience("cortex", development_units=4, learning_units=3,
                           expected_revision=sidecar.revision)
        self.assertEqual(sidecar.region("cortex")["mature_neurons"], 13)
        self.assertEqual(sidecar.region("cortex")["immature_neurons"], 0)
        self.assertTrue(sidecar.region("cortex")["structurally_mature"])
        self.assertEqual(sidecar.region("cortex")["learning_progress"], 3)
        self.assertEqual(canonical.snapshot(), canonical_before)
        self.assertEqual(canonical_before["schema"], CANONICAL_SCHEMA)
        self.assertEqual(sidecar.snapshot()["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(Lifecycle.from_snapshot(sidecar.snapshot()).snapshot(),
                         sidecar.snapshot())

    def test_snapshot_formats_are_not_implicitly_interchangeable(self):
        canonical = DevelopmentClock("compatibility-fixture", "a" * 64)
        sidecar = Lifecycle(PROFILE)
        with self.assertRaises(CanonicalError):
            DevelopmentClock.from_snapshot(sidecar.snapshot())
        with self.assertRaises(EventError):
            Lifecycle.from_snapshot(canonical.snapshot())

    def test_stale_revision_is_atomic_and_no_age_based_pruning(self):
        sidecar = Lifecycle(PROFILE)
        revision = sidecar.revision
        sidecar.advance(1000000, expected_revision=revision)
        self.assertEqual(sidecar.region("cortex")["mature_neurons"], 5)
        self.assertEqual(sidecar.region("cortex")["developmental_synapses_pruned"], 0)
        state = sidecar.snapshot()
        with self.assertRaises(EventError):
            sidecar.experience("cortex", development_units=4,
                               expected_revision=revision)
        self.assertEqual(state, sidecar.snapshot())


if __name__ == "__main__":
    unittest.main()
