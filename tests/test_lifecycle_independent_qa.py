"""Independent stdlib contract QA for the opt-in GENESIS lifecycle R0.1.

Black-box tests for distinct synthetic region schedules and failure atomicity.
"""

import copy
import json
import unittest

from mojum.lifecycle_ledger import (
    Lifecycle, LifecycleError, MAX_TICKS, MAX_UNITS, PROFILE_SCHEMA,
)


def profile():
    base = {
        "initial_mature": 20,
        "initial_immature": 8,
        "newborn_cap": 4,
        "birth_per_evidence": 1,
        "mature_per_evidence": 1,
        "synapses_initial": 20,
        "synapses_cap": 80,
        "synapses_per_learning": 2,
        "prune_per_evidence": 0,
        "prune_cap": 3,
    }
    cortex = copy.deepcopy(base)
    cerebellum = dict(base, birth_per_evidence=0,
                      mature_per_evidence=2, prune_per_evidence=1)
    dentate = dict(base, birth_per_evidence=2,
                   mature_per_evidence=4, prune_per_evidence=2)
    return {
        "schema": PROFILE_SCHEMA,
        "organism_id": "lifecycle-qa-fixture-001",
        "senescence_enabled": False,
        "regions": {
            "cortex": cortex,
            "cerebellum": cerebellum,
            "dentate_gyrus": dentate,
        },
    }


class IndependentLifecycleContractQA(unittest.TestCase):
    def test_three_distinct_region_schedules_are_evidence_gated(self):
        life = Lifecycle(profile())
        life.advance(1_000_000, expected_revision=life.revision)
        self.assertTrue(all(life.region(r)["development_progress"] == 0
                            for r in life.profile["regions"]))
        for name in ("cortex", "cerebellum", "dentate_gyrus"):
            life.experience(name, development_units=2,
                            expected_revision=life.revision)
        expected = {
            "cortex": (22, 8, 2, 0, 20),
            "cerebellum": (24, 4, 0, 2, 18),
            "dentate_gyrus": (28, 4, 4, 3, 17),
        }
        for name, (mature, immature, newborn, pruned, synapses) in expected.items():
            with self.subTest(name=name):
                state = life.region(name)
                self.assertEqual(state["mature_neurons"], mature)
                self.assertEqual(state["immature_neurons"], immature)
                self.assertEqual(state["newborn_created"], newborn)
                self.assertEqual(state["developmental_synapses_pruned"], pruned)
                self.assertEqual(state["synapses"], synapses)
                self.assertEqual(state["development_progress"], 2)

    def test_elapsed_time_cannot_change_post_maturity_structure_or_learning(self):
        life = Lifecycle(profile())
        for region in life.profile["regions"]:
            life.experience(region, development_units=12, learning_units=3,
                            expected_revision=life.revision)
        baseline = {r: life.region(r) for r in life.profile["regions"]}
        self.assertTrue(all(v["structurally_mature"] for v in baseline.values()))
        life.advance(10**12, expected_revision=life.revision)
        life.advance(10**12, expected_revision=life.revision)
        for name, before in baseline.items():
            self.assertEqual(life.region(name), before)
        life.experience("cortex", learning_units=4,
                        expected_revision=life.revision)
        after = life.region("cortex")
        self.assertEqual(after["mature_neurons"], baseline["cortex"]["mature_neurons"])
        self.assertEqual(after["synapses"], baseline["cortex"]["synapses"] + 8)
        self.assertEqual(after["learning_progress"], 7)
        cold = Lifecycle.from_snapshot(json.loads(json.dumps(life.snapshot())))
        self.assertEqual(cold.snapshot(), life.snapshot())
        self.assertEqual(cold.profile["organism_id"], "lifecycle-qa-fixture-001")

    def test_rejected_updates_never_change_revision_or_clock(self):
        life = Lifecycle(profile())
        life.experience("cortex", development_units=1,
                        expected_revision=life.revision)
        before = life.snapshot()
        mutations = (
            lambda: life.advance(1, expected_revision="stale"),
            lambda: life.advance(True, expected_revision=life.revision),
            lambda: life.advance(MAX_TICKS + 1, expected_revision=life.revision),
            lambda: life.experience("unknown", learning_units=1,
                                    expected_revision=life.revision),
            lambda: life.experience("cortex", development_units=MAX_UNITS,
                                    expected_revision=life.revision),
            lambda: life.experience("cortex", learning_units=float("inf"),
                                    expected_revision=life.revision),
        )
        for mutation in mutations:
            with self.assertRaises(LifecycleError):
                mutation()
            self.assertEqual(life.snapshot(), before)

    def test_cold_replay_fails_closed_on_wrong_time_and_schema(self):
        life = Lifecycle(profile())
        life.advance(20, expected_revision=life.revision)
        life.experience("cortex", development_units=1,
                        expected_revision=life.revision)
        original = life.snapshot()
        for altered in (
            lambda s: s["events"][1].update(at_tick=19),
            lambda s: s["events"][0].update(ticks=21),
            lambda s: s.update(tick=21),
            lambda s: s.update(schema="mojum.lifecycle.snapshot/0"),
            lambda s: s.update(trust="authenticated"),
            lambda s: s["events"][1].update(unknown=True),
        ):
            damaged = copy.deepcopy(original)
            altered(damaged)
            with self.assertRaises(LifecycleError):
                Lifecycle.from_snapshot(damaged)
        self.assertEqual(life.snapshot(), original)

    def test_senescence_cannot_be_silently_enabled(self):
        original = profile()
        for value in (True, 0, None, "false"):
            bad = copy.deepcopy(original)
            bad["senescence_enabled"] = value
            with self.subTest(value=value), self.assertRaises(LifecycleError):
                Lifecycle(bad)
        life = Lifecycle(original)
        self.assertIs(life.profile["senescence_enabled"], False)

    def test_bounded_learning_counter_does_not_wrap_or_kill_neurons(self):
        life = Lifecycle(profile())
        life.experience("cortex", development_units=12,
                        learning_units=MAX_UNITS, expected_revision=life.revision)
        before = life.snapshot()
        observed = life.region("cortex")
        self.assertEqual(observed["learning_progress"], MAX_UNITS)
        self.assertEqual(observed["synapses"], 80)
        with self.assertRaises(LifecycleError):
            life.experience("cortex", learning_units=1,
                            expected_revision=life.revision)
        self.assertEqual(life.snapshot(), before)
        life.advance(MAX_TICKS, expected_revision=life.revision)
        self.assertEqual(life.region("cortex"), observed)
        with self.assertRaises(LifecycleError):
            life.advance(1, expected_revision=life.revision)


if __name__ == "__main__":
    unittest.main()
