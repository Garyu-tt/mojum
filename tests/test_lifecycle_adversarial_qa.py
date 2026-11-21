"""Independent adversarial QA for opt-in GENESIS lifecycle fixture."""
import copy
import unittest

from mojum.lifecycle import (
    Lifecycle, LifecycleError, MAX_TICKS, MAX_UNITS, PROFILE_SCHEMA, _hash,
)


REGION = {
    "initial_mature": 5, "initial_immature": 4,
    "newborn_cap": 3, "birth_per_evidence": 1,
    "mature_per_evidence": 2, "synapses_initial": 10,
    "synapses_cap": 50, "synapses_per_learning": 1,
    "prune_per_evidence": 1, "prune_cap": 2,
}


def profile(organism_id="dante-fixture"):
    return {
        "schema": PROFILE_SCHEMA,
        "organism_id": organism_id,
        "senescence_enabled": False,
        "regions": {"cortex": copy.deepcopy(REGION),
                    "cerebellum": copy.deepcopy(REGION)},
    }


class LifecycleAdversarialQATests(unittest.TestCase):
    def test_unpaired_surrogate_ids_fail_as_contract_errors_at_boundary(self):
        # json.loads accepts these escapes; encode('utf-8') rejects them.
        for invalid in ("\\ud800", "\\udc00", "safe\\ud800unsafe", "\\ud83d\\ude80"):
            invalid = invalid.encode("ascii").decode("unicode_escape")
            with self.subTest(value=repr(invalid)):
                with self.assertRaisesRegex(LifecycleError, "invalid Unicode"):
                    Lifecycle(profile(invalid))

    def test_unicode_organism_id_can_still_roundtrip(self):
        organism_id = "Данте-✨"
        clock = Lifecycle(profile(organism_id))
        clock.advance(2, expected_revision=clock.revision)
        restored = Lifecycle.from_snapshot(clock.snapshot())
        self.assertEqual(restored.profile["organism_id"], organism_id)
        self.assertEqual(restored.revision, clock.revision)

    def test_poisoned_checkpoint_identity_fails_without_unicode_crash(self):
        clock = Lifecycle(profile())
        snapshot = clock.snapshot()
        snapshot["profile"]["organism_id"] = "\\ud800".encode("ascii").decode("unicode_escape")
        with self.assertRaisesRegex(LifecycleError, "invalid Unicode"):
            Lifecycle.from_snapshot(snapshot)

    def test_independent_region_counters_and_unbounded_age_neutrality(self):
        clock = Lifecycle(profile())
        clock.experience("cortex", development_units=9, learning_units=3,
                         expected_revision=clock.revision)
        cortex_before = clock.region("cortex")
        cerebellum_before = clock.region("cerebellum")
        clock.advance(MAX_TICKS, expected_revision=clock.revision)
        self.assertEqual(clock.region("cortex"), cortex_before)
        self.assertEqual(clock.region("cerebellum"), cerebellum_before)
        self.assertTrue(clock.region("cortex")["structurally_mature"])
        clock.experience("cerebellum", learning_units=4,
                         expected_revision=clock.revision)
        self.assertEqual(clock.region("cortex"), cortex_before)
        self.assertEqual(clock.region("cerebellum")["learning_progress"], 4)
        self.assertEqual(Lifecycle.from_snapshot(clock.snapshot()).tick, MAX_TICKS)

    def test_region_with_births_disabled_can_mature_initial_cohort(self):
        configured = profile()
        configured["regions"]["cerebellum"]["birth_per_evidence"] = 0
        clock = Lifecycle(configured)
        clock.experience("cerebellum", development_units=1, expected_revision=clock.revision)
        self.assertFalse(clock.region("cerebellum")["structurally_mature"])

        clock.experience("cerebellum", development_units=1, expected_revision=clock.revision)
        baseline = clock.region("cerebellum")
        self.assertEqual(baseline["immature_neurons"], 0)
        self.assertEqual(baseline["newborn_created"], 0)
        self.assertTrue(baseline["structurally_mature"])

        clock.advance(MAX_TICKS, expected_revision=clock.revision)
        self.assertEqual(Lifecycle.from_snapshot(clock.snapshot()).region("cerebellum"), baseline)

    def test_overflow_rejection_keeps_original_revision_and_other_gate(self):
        clock = Lifecycle(profile())
        clock.experience("cortex", learning_units=MAX_UNITS,
                         expected_revision=clock.revision)
        original = clock.snapshot()
        with self.assertRaisesRegex(LifecycleError, "capacity"):
            clock.experience("cortex", learning_units=1,
                             expected_revision=clock.revision)
        self.assertEqual(clock.snapshot(), original)
        clock.experience("cortex", development_units=1,
                         expected_revision=clock.revision)
        self.assertEqual(clock.region("cortex")["learning_progress"], MAX_UNITS)
        self.assertEqual(clock.region("cortex")["development_progress"], 1)

    def test_recomputed_checksum_cannot_hide_forged_tick_metadata(self):
        clock = Lifecycle(profile())
        clock.advance(1, expected_revision=clock.revision)
        clock.experience("cortex", learning_units=2, expected_revision=clock.revision)
        snapshot = clock.snapshot()
        snapshot["events"][1]["at_tick"] = True  # bool == 1, but not canonical int
        snapshot["revision"] = _hash({"profile": snapshot["profile"], "events": snapshot["events"]})
        with self.assertRaises(LifecycleError):
            Lifecycle.from_snapshot(snapshot)

    def test_original_checksum_cannot_hide_non_integer_tick_metadata(self):
        for tick, invalid_values in ((0, (False, 0.0)), (1, (True, 1.0))):
            clock = Lifecycle(profile())
            if tick:
                clock.advance(tick, expected_revision=clock.revision)
            clock.experience("cortex", learning_units=1, expected_revision=clock.revision)
            original = clock.snapshot()
            self.assertEqual(Lifecycle.from_snapshot(original).snapshot(), original)

            for invalid in invalid_values:
                snapshot = copy.deepcopy(original)
                snapshot["events"][-1]["at_tick"] = invalid
                with self.subTest(tick=tick, invalid=invalid):
                    with self.assertRaisesRegex(LifecycleError, "experience tick"):
                        Lifecycle.from_snapshot(snapshot)

    def test_replay_with_interleaved_regions_preserves_exact_state(self):
        clock = Lifecycle(profile())
        for i in range(32):
            clock.advance(i + 1, expected_revision=clock.revision)
            clock.experience("cortex" if i % 2 else "cerebellum",
                             development_units=i % 3, learning_units=1,
                             expected_revision=clock.revision)
        restored = Lifecycle.from_snapshot(clock.snapshot())
        self.assertEqual(restored.snapshot(), clock.snapshot())
        for name in profile()["regions"]:
            self.assertEqual(restored.region(name), clock.region(name))


if __name__ == "__main__":
    unittest.main()
