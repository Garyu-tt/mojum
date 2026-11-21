"""CPU-only independent tests for the synthetic developmental clock."""
import copy
import unittest

from mojum.lifecycle import (DEFAULT_PROFILE, DevelopmentClock, LifecycleError,
                             MAX_COUNTER, REGIONS, _digest)

GENOME_REV = "a" * 64
EVIDENCE = {name: 20 for name in REGIONS}


class LifecycleTests(unittest.TestCase):
    def new_clock(self):
        return DevelopmentClock("dante-fixture-001", GENOME_REV)

    def test_three_regions_grow_independently(self):
        clock = self.new_clock()
        clock.advance(EVIDENCE)
        sizes = {r: clock.regions[r]["mature"] for r in REGIONS}
        self.assertEqual(sizes, {"cortex": 4, "cerebellum": 6, "dentate_gyrus": 2})
        self.assertEqual(clock.tick, 1)

    def test_experience_gate_prevents_maturation_not_birth(self):
        clock = self.new_clock()
        for _ in range(4):
            clock.advance({region: 0 for region in REGIONS})
        self.assertEqual(clock.regions["cortex"]["mature"], 2)
        self.assertGreater(clock.regions["cortex"]["immature"], 2)
        self.assertEqual(clock.regions["cerebellum"]["mature"], 4)

    def test_no_age_degradation_after_maturity_but_observation_continues(self):
        early = self.new_clock()
        for _ in range(50):
            early.advance(EVIDENCE)
        baseline = {r: (early.regions[r]["mature"], early.regions[r]["immature"])
                    for r in REGIONS}
        self.assertEqual(baseline, {r: (DEFAULT_PROFILE[r]["target_mature"], 0)
                                    for r in REGIONS})
        old_observations = {r: early.regions[r]["observations_total"] for r in REGIONS}
        old_post_maturity = {r: early.regions[r]["post_maturity_observations"] for r in REGIONS}
        older = DevelopmentClock.from_snapshot(early.snapshot())
        for _ in range(500):
            older.advance(EVIDENCE)
        self.assertEqual({r: (older.regions[r]["mature"], older.regions[r]["immature"])
                          for r in REGIONS}, baseline)
        for r in REGIONS:
            self.assertGreater(older.regions[r]["observations_total"], old_observations[r])
            self.assertEqual(older.regions[r]["post_maturity_observations"],
                             old_post_maturity[r] + 500 * EVIDENCE[r])
        self.assertEqual(older.organism_id, early.organism_id)
        self.assertEqual(older.genome_revision, early.genome_revision)

    def test_restart_exact_replay(self):
        a, b = self.new_clock(), self.new_clock()
        signal_seq = [{r: (idx + i) % 7 for i, r in enumerate(REGIONS)}
                      for idx in range(27)]
        for observations in signal_seq:
            a.advance(observations)
        for observations in signal_seq[:11]:
            b.advance(observations)
        b = DevelopmentClock.from_snapshot(b.snapshot())
        for observations in signal_seq[11:]:
            b.advance(observations)
        self.assertEqual(a.snapshot(), b.snapshot())

    def test_atomic_failed_signal_and_counter_overflow(self):
        clock = self.new_clock()
        old = clock.snapshot()
        for invalid in ({"cortex": True, "cerebellum": 2, "dentate_gyrus": 2},
                        {"cortex": float("nan"), "cerebellum": 2, "dentate_gyrus": 2},
                        {"cortex": 10001, "cerebellum": 2, "dentate_gyrus": 2},
                        {"cortex": 2, "cerebellum": 2}):
            with self.assertRaises(LifecycleError):
                clock.advance(invalid)
            self.assertEqual(clock.snapshot(), old)
        clock.tick = MAX_COUNTER
        with self.assertRaisesRegex(LifecycleError, "clock overflow"):
            clock.advance(EVIDENCE)
        self.assertEqual(clock.tick, MAX_COUNTER)

    def test_corruption_and_forged_invariants_rejected(self):
        clock = self.new_clock()
        snap = clock.snapshot()
        bad = copy.deepcopy(snap)
        bad["regions"]["cortex"]["mature"] = 999
        with self.assertRaisesRegex(LifecycleError, "changed"):
            DevelopmentClock.from_snapshot(bad)
        bad["sha256"] = _digest({k: v for k, v in bad.items() if k != "sha256"})
        with self.assertRaisesRegex(LifecycleError, "inconsistent"):
            DevelopmentClock.from_snapshot(bad)
        impossible = copy.deepcopy(snap)
        impossible["regions"]["cortex"]["mature"] += 1
        impossible["sha256"] = _digest({k: v for k, v in impossible.items()
                                        if k != "sha256"})
        with self.assertRaisesRegex(LifecycleError, "inconsistent"):
            DevelopmentClock.from_snapshot(impossible)

    def test_forged_post_maturity_count_before_maturity_rejected(self):
        clock = self.new_clock()
        clock.advance(EVIDENCE)
        pristine = clock.snapshot()
        bad = copy.deepcopy(pristine)
        self.assertLess(bad["regions"]["cortex"]["mature"],
                        bad["profile"]["cortex"]["target_mature"])
        bad["regions"]["cortex"]["post_maturity_observations"] = 1
        bad["sha256"] = _digest({k: v for k, v in bad.items() if k != "sha256"})
        with self.assertRaisesRegex(LifecycleError, "inconsistent"):
            DevelopmentClock.from_snapshot(bad)
        self.assertEqual(clock.snapshot(), pristine)

    def test_profile_validation_and_snapshot_isolation(self):
        profile = copy.deepcopy(DEFAULT_PROFILE)
        profile["cortex"]["birth_per_tick"] = 0
        with self.assertRaises(LifecycleError):
            DevelopmentClock("dante-fixture-001", GENOME_REV, profile=profile)
        clock = self.new_clock()
        snap = clock.snapshot()
        snap["profile"]["cortex"]["target_mature"] = 999999
        snap["regions"]["cortex"]["mature"] = 999
        self.assertEqual(clock.profile["cortex"]["target_mature"], 14)
        self.assertEqual(clock.regions["cortex"]["mature"], 2)


if __name__ == "__main__":
    unittest.main()
