import copy
import unittest

from mojum.development import (
    DevelopmentError, MAX_COUNTER, REGIONS, advance, initial_state,
    metrics, restore, snapshot, synthetic_program,
)


class DevelopmentFixtureTests(unittest.TestCase):
    def setUp(self):
        self.program = synthetic_program()
        self.state = initial_state(self.program, "dante-fixture-001", "synthetic-genome-sha")
        self.none = {r: 0 for r in REGIONS}
        self.all = {r: 1 for r in REGIONS}

    def test_zero_experience_changes_only_elapsed_clock(self):
        future = self.state
        for _ in range(100):
            future = advance(future, self.program, self.none)
        self.assertEqual(future["elapsed_ticks"], 100)
        self.assertEqual(future["regions"], self.state["regions"])
        self.assertEqual(future["senescence_mode"], "disabled")

    def test_three_region_growth_curves_are_independent(self):
        future = self.state
        for _ in range(6):
            future = advance(future, self.program, {"cortex": 1, "cerebellum": 1, "dentate_gyrus": 0})
        self.assertGreater(future["regions"]["cortex"]["mature"], self.state["regions"]["cortex"]["mature"])
        self.assertGreater(future["regions"]["cerebellum"]["mature"], self.state["regions"]["cerebellum"]["mature"])
        self.assertEqual(future["regions"]["dentate_gyrus"], self.state["regions"]["dentate_gyrus"])
        self.assertNotEqual(metrics(future, self.program)["regions"]["cortex"]["maturity_milli"],
                            metrics(future, self.program)["regions"]["cerebellum"]["maturity_milli"])

    def test_newborn_cannot_mature_during_birth_tick(self):
        s = advance(self.state, self.program, self.all)
        self.assertEqual(s["regions"]["dentate_gyrus"]["newborn"], 2)
        self.assertEqual(s["regions"]["dentate_gyrus"]["immature"], 2)
        s2 = advance(s, self.program, self.all)
        self.assertEqual(s2["regions"]["dentate_gyrus"]["immature"], 3)

    def test_pruning_affects_synapses_not_neuron_survival(self):
        p = synthetic_program()
        p["regions"]["cerebellum"]["synaptogenesis_per_experience"] = 0
        p["regions"]["cerebellum"]["pruning_per_experience"] = 2
        s = initial_state(p, "example", "rev")
        next_s = advance(s, p, self.all)
        self.assertLess(next_s["regions"]["cerebellum"]["synapses"], s["regions"]["cerebellum"]["synapses"])
        self.assertEqual(sum(next_s["regions"]["cerebellum"][k] for k in ("mature", "immature", "newborn")),
                         sum(s["regions"]["cerebellum"][k] for k in ("mature", "immature", "newborn")) + 2)

    def test_adult_never_age_declines_and_still_learns(self):
        s = self.state
        for _ in range(100):
            s = advance(s, self.program, self.all)
        before = metrics(s, self.program)
        for name in REGIONS:
            self.assertEqual(before["regions"][name]["maturity_milli"], 1000)
        no_stimulus = s
        for _ in range(500):
            no_stimulus = advance(no_stimulus, self.program, self.none)
        self.assertEqual(no_stimulus["regions"], s["regions"])
        later = advance(no_stimulus, self.program, self.all)
        for name in REGIONS:
            self.assertGreater(later["regions"][name]["learning_events"], s["regions"][name]["learning_events"])
            self.assertEqual(metrics(later, self.program)["regions"][name]["plasticity_milli"],
                             before["regions"][name]["plasticity_milli"])

    def test_restart_preserves_identity_and_exact_trajectory(self):
        control = self.state
        for _ in range(33):
            control = advance(control, self.program, self.all)
        part = self.state
        for _ in range(12):
            part = advance(part, self.program, self.all)
        resumed = restore(snapshot(part, self.program), self.program)
        for _ in range(21):
            resumed = advance(resumed, self.program, self.all)
        self.assertEqual(resumed, control)
        self.assertEqual(resumed["organism_id"], "dante-fixture-001")
        self.assertEqual(resumed["genome_revision"], "synthetic-genome-sha")

    def test_twins_diverge_without_identity_conflation(self):
        a = initial_state(self.program, "twin-one", "same-genome")
        b = initial_state(self.program, "twin-two", "same-genome")
        for _ in range(7):
            a = advance(a, self.program, self.all)
            b = advance(b, self.program, self.none)
        self.assertNotEqual(a["regions"], b["regions"])
        self.assertNotEqual(a["organism_id"], b["organism_id"])
        self.assertEqual(a["genome_revision"], b["genome_revision"])

    def test_budgets_never_exceeded_after_many_exposures(self):
        s = self.state
        for _ in range(200):
            s = advance(s, self.program, self.all)
        for name in REGIONS:
            region, p = s["regions"][name], self.program["regions"][name]
            self.assertLessEqual(sum(region[k] for k in ("immature", "newborn", "mature")), p["neuron_budget"])
            self.assertLessEqual(region["synapses"], p["synapse_budget"])

    def test_invalid_evidence_does_not_modify_input(self):
        original = copy.deepcopy(self.state)
        for bad in (True, -1, 2, 1.0, float("nan"), "1"):
            with self.assertRaises(DevelopmentError):
                advance(self.state, self.program, {"cortex": bad, "cerebellum": 0, "dentate_gyrus": 0})
        self.assertEqual(self.state, original)

    def test_noncanonical_region_and_experimental_senescence_rejected(self):
        s = copy.deepcopy(self.state)
        s["senescence_mode"] = "experimental"
        with self.assertRaises(DevelopmentError):
            advance(s, self.program, self.none)
        s = copy.deepcopy(self.state)
        s["regions"]["cortex"]["mature"] = True
        with self.assertRaises(DevelopmentError):
            advance(s, self.program, self.none)
        p = synthetic_program()
        p["regions"]["cortex"]["neuron_budget"] = False
        with self.assertRaises(DevelopmentError):
            initial_state(p, "x", "rev")

    def test_reject_unpaired_surrogate_identities_before_bootstrap_or_snapshot(self):
        for field in ("organism_id", "genome_revision"):
            for bad in ("\\ud800", "\\udfff"):
                with self.subTest(field=field, bad=repr(bad)):
                    args = {"organism_id": "fixture", "genome_revision": "synthetic-rev"}
                    args[field] = bad
                    with self.assertRaisesRegex(DevelopmentError, "UTF-8"):
                        initial_state(self.program, **args)
                    invalid = copy.deepcopy(self.state)
                    invalid[field] = bad
                    with self.assertRaisesRegex(DevelopmentError, "UTF-8"):
                        snapshot(invalid, self.program)
                    saved = snapshot(self.state, self.program)
                    saved["state"][field] = bad
                    with self.assertRaises(DevelopmentError):
                        restore(saved, self.program)

    def test_tamper_detection_and_program_pin(self):
        record = snapshot(self.state, self.program)
        record["state"]["regions"]["cortex"]["mature"] += 1
        with self.assertRaisesRegex(DevelopmentError, "checksum"):
            restore(record, self.program)
        record = snapshot(self.state, self.program)
        modified = synthetic_program()
        modified["regions"]["cortex"]["plasticity_milli"] -= 1
        with self.assertRaisesRegex(DevelopmentError, "program mismatch"):
            restore(record, modified)

    def test_overflow_rejected_without_mutation(self):
        s = copy.deepcopy(self.state)
        s["elapsed_ticks"] = MAX_COUNTER
        with self.assertRaisesRegex(DevelopmentError, "overflow"):
            advance(s, self.program, self.none)
        self.assertEqual(s["elapsed_ticks"], MAX_COUNTER)
        s = copy.deepcopy(self.state)
        s["elapsed_ticks"] = MAX_COUNTER
        s["regions"]["cortex"]["learning_events"] = MAX_COUNTER
        with self.assertRaises(DevelopmentError):
            advance(s, self.program, self.all)


if __name__ == "__main__":
    unittest.main()
