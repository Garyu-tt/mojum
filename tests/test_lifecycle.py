"""CPU-only synthetic anti-senescence and replay contract tests."""
import copy
import json
import unittest

from mojum.lifecycle import Lifecycle, LifecycleError, MAX_EVENTS, MAX_TICKS, PROFILE_SCHEMA


CONFIG = {
    "initial_mature": 20, "initial_immature": 8,
    "newborn_cap": 4, "birth_per_evidence": 1,
    "mature_per_evidence": 3, "synapses_initial": 20,
    "synapses_cap": 80, "synapses_per_learning": 2,
    "prune_per_evidence": 1, "prune_cap": 3,
}
PROFILE = {"schema": PROFILE_SCHEMA, "organism_id": "dante-fixture-001",
           "senescence_enabled": False,
           "regions": {n: copy.deepcopy(CONFIG) for n in
                       ("cortex", "cerebellum", "dentate_gyrus")}}


class LifecycleTests(unittest.TestCase):
    def make(self):
        return Lifecycle(copy.deepcopy(PROFILE))

    def test_growing_three_regions_requires_evidence_not_wall_time(self):
        state = self.make()
        state.advance(20_000, expected_revision=state.revision)
        for name in PROFILE["regions"]:
            self.assertEqual(state.region(name)["mature_neurons"], 20)
        for n, dev in (("cortex", 1), ("cerebellum", 2), ("dentate_gyrus", 5)):
            state.experience(n, development_units=dev, expected_revision=state.revision)
        self.assertEqual([state.region(n)["mature_neurons"] for n in PROFILE["regions"]],
                         [23, 26, 32])
        self.assertTrue(state.region("dentate_gyrus")["structurally_mature"])

    def test_no_age_triggered_decline_after_maturity(self):
        base = self.make()
        for n in PROFILE["regions"]:
            base.experience(n, development_units=6, expected_revision=base.revision)
        older = Lifecycle.from_snapshot(base.snapshot())
        older.advance(10**12, expected_revision=older.revision)
        self.assertEqual(older.tick, 10**12)
        for n in PROFILE["regions"]:
            self.assertEqual(older.region(n), base.region(n))

    def test_no_birth_region_reaches_structural_maturity(self):
        """A zero birth rate must not reserve capacity for impossible newborns."""
        profile = copy.deepcopy(PROFILE)
        cfg = profile["regions"]["cortex"]
        cfg["birth_per_evidence"] = 0
        cfg["newborn_cap"] = 4
        cfg["mature_per_evidence"] = 2
        state = Lifecycle(profile)
        state.experience("cortex", development_units=3,
                         expected_revision=state.revision)
        self.assertFalse(state.region("cortex")["structurally_mature"])
        state.experience("cortex", development_units=1,
                         expected_revision=state.revision)
        observed = state.region("cortex")
        self.assertEqual(observed["newborn_created"], 0)
        self.assertEqual(observed["mature_neurons"], 28)
        self.assertEqual(observed["immature_neurons"], 0)
        self.assertTrue(observed["structurally_mature"])
        state.advance(10**12, expected_revision=state.revision)
        self.assertEqual(state.region("cortex"), observed)
        self.assertEqual(Lifecycle.from_snapshot(state.snapshot()).region("cortex"), observed)

    def test_learning_continues_after_structural_maturity(self):
        s = self.make()
        s.experience("cortex", development_units=6, expected_revision=s.revision)
        before = s.region("cortex")
        self.assertTrue(before["structurally_mature"])
        s.advance(10_000_000, expected_revision=s.revision)
        s.experience("cortex", learning_units=7, expected_revision=s.revision)
        after = s.region("cortex")
        self.assertEqual(after["mature_neurons"], before["mature_neurons"])
        self.assertEqual(after["synapses"] - before["synapses"], 14)
        self.assertEqual(after["learning_progress"], 7)

    def test_cold_replay_and_no_external_mutation(self):
        s = self.make()
        s.advance(14, expected_revision=s.revision)
        s.experience("cortex", development_units=5, learning_units=4,
                     expected_revision=s.revision)
        snap = json.loads(json.dumps(s.snapshot()))
        copy_s = Lifecycle.from_snapshot(snap)
        self.assertEqual(copy_s.snapshot(), snap)
        snap["profile"]["organism_id"] = "intruder"
        snap["events"][0]["ticks"] = 99
        self.assertEqual(copy_s.profile["organism_id"], "dante-fixture-001")
        self.assertEqual(copy_s.tick, 14)

    def test_identity_and_profile_not_publicly_mutable(self):
        s = self.make()
        exposed = s.profile
        exposed["organism_id"] = "intruder"
        exposed["regions"]["cortex"]["initial_mature"] = 0
        self.assertEqual(s.profile["organism_id"], "dante-fixture-001")
        self.assertEqual(s.region("cortex")["initial_mature"], 20)
        with self.assertRaises(AttributeError):
            s.tick = 123
        with self.assertRaises(AttributeError):
            s.profile = exposed

    def test_detect_tamper_and_disallow_age_policy_migration(self):
        s = self.make()
        s.advance(4, expected_revision=s.revision)
        snap = s.snapshot()
        snap["events"][0]["ticks"] = 5
        with self.assertRaises(LifecycleError):
            Lifecycle.from_snapshot(snap)
        bad = copy.deepcopy(PROFILE)
        bad["senescence_enabled"] = True
        with self.assertRaises(LifecycleError):
            Lifecycle(bad)

    def test_reject_forged_event_and_bad_counts(self):
        s = self.make()
        for bad in (True, 1.2, "3", -1, MAX_TICKS + 1):
            with self.assertRaises(LifecycleError):
                s.advance(bad, expected_revision=s.revision)
        for bad in (True, 1.0, -1, 10_000_001):
            with self.assertRaises(LifecycleError):
                s.experience("cortex", learning_units=bad,
                             expected_revision=s.revision)
        with self.assertRaises(LifecycleError):
            s.experience("wrong", learning_units=1, expected_revision=s.revision)
        with self.assertRaises(LifecycleError):
            s.experience("cortex", expected_revision=s.revision)
        with self.assertRaises(LifecycleError):
            s.advance(1, expected_revision="stale")
        self.assertEqual(s.tick, 0)
        self.assertEqual(s.snapshot()["events"], [])

    def test_overflow_event_bounds_and_strict_schema(self):
        s = self.make()
        s.advance(MAX_TICKS, expected_revision=s.revision)
        with self.assertRaises(LifecycleError):
            s.advance(1, expected_revision=s.revision)
        bad = copy.deepcopy(PROFILE)
        bad["regions"]["cortex"]["code"] = "unknown"
        with self.assertRaises(LifecycleError):
            Lifecycle(bad)
        malformed = s.snapshot()
        malformed["events"][0]["unknown"] = True
        with self.assertRaises(LifecycleError):
            Lifecycle.from_snapshot(malformed)
        self.assertEqual(MAX_EVENTS, 4096)


if __name__ == "__main__":
    unittest.main()
