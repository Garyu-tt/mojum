import unittest
from dataclasses import replace

from mojum.neural_lod import (
    CLASSIFICATION,
    LODPlan,
    NeuralLODError,
    PopulationLOD,
    plan_lod,
    verify_plan,
)


def row(name, importance=100, costs=(1, 3, 5)):
    return {
        "population_id": name,
        "importance_milli": importance,
        "coarse_cost_units": costs[0],
        "medium_cost_units": costs[1],
        "fine_cost_units": costs[2],
    }


def plan(rows, budget):
    return plan_lod(
        rows,
        budget,
        classification=CLASSIFICATION,
        consent=True,
    )


class NeuralLODTests(unittest.TestCase):
    def test_baseline_allocates_coarse_to_every_population(self):
        result = plan([row("b"), row("a")], 2)
        self.assertEqual(
            [(item.population_id, item.level) for item in result.populations],
            [("a", "coarse"), ("b", "coarse")],
        )
        self.assertEqual(
            (result.baseline_cost_units, result.used_cost_units,
             result.remaining_cost_units, result.upgrades),
            (2, 2, 0, 0),
        )
        self.assertTrue(verify_plan(result))

    def test_upgrades_follow_deterministic_importance_per_cost(self):
        result = plan(
            [row("b", 50, (1, 2, 3)), row("a", 100, (1, 3, 5))],
            5,
        )
        levels = {item.population_id: item.level for item in result.populations}
        self.assertEqual(levels, {"a": "medium", "b": "medium"})
        self.assertEqual(result.used_cost_units, 5)
        self.assertEqual(result.upgrades, 2)

    def test_tie_break_is_population_id_not_input_order(self):
        first = plan([row("z"), row("a")], 4)
        second = plan([row("a"), row("z")], 4)
        self.assertEqual(first, second)
        self.assertEqual(
            [(item.population_id, item.level) for item in first.populations],
            [("a", "medium"), ("z", "coarse")],
        )

    def test_zero_importance_stays_at_coarse(self):
        result = plan([row("quiet", 0)], 100)
        self.assertEqual(result.populations[0].level, "coarse")
        self.assertEqual(result.used_cost_units, 1)

    def test_unused_budget_is_reported_when_no_upgrade_fits(self):
        result = plan([row("a", 100, (1, 5, 9))], 4)
        self.assertEqual(result.populations[0].level, "coarse")
        self.assertEqual(result.remaining_cost_units, 3)

    def test_input_order_does_not_change_digest(self):
        a = row("a", 100, (2, 4, 8))
        b = row("b", 200, (1, 3, 6))
        self.assertEqual(plan([a, b], 10), plan([b, a], 10))

    def test_receipt_is_frozen_and_detached(self):
        result = plan([row("a")], 3)
        with self.assertRaises((AttributeError, TypeError)):
            result.used_cost_units = 99
        with self.assertRaises((AttributeError, TypeError)):
            result.populations[0].level = "fine"
        self.assertTrue(verify_plan(result))

    def test_digest_detects_modified_receipt(self):
        result = plan([row("a")], 3)
        changed = replace(result, used_cost_units=result.used_cost_units + 1)
        with self.assertRaisesRegex(NeuralLODError, "accounting|used_cost_units"):
            verify_plan(changed)
        changed_item = replace(result.populations[0], level="fine")
        changed = replace(result, populations=(changed_item,))
        with self.assertRaisesRegex(NeuralLODError, "accounting|digest"):
            verify_plan(changed)

    def test_missing_baseline_budget_is_rejected(self):
        with self.assertRaisesRegex(NeuralLODError, "coarse baseline"):
            plan([row("a", costs=(3, 4, 5)), row("b", costs=(2, 3, 4))], 4)

    def test_explicit_public_synthetic_and_consent_are_required(self):
        with self.assertRaisesRegex(NeuralLODError, "PUBLIC_SYNTHETIC"):
            plan_lod([row("a")], 3, classification="PRIVATE", consent=True)
        with self.assertRaisesRegex(NeuralLODError, "consent"):
            plan_lod([row("a")], 3, classification=CLASSIFICATION, consent=False)
        with self.assertRaises(NeuralLODError):
            plan_lod([row("a")], 3, classification=CLASSIFICATION, consent=1)

    def test_native_exact_types_are_required(self):
        bad = row("a")
        bad["importance_milli"] = True
        with self.assertRaisesRegex(NeuralLODError, "native integer"):
            plan([bad], 3)
        with self.assertRaises(NeuralLODError):
            plan_lod((row("a"),), 3, classification=CLASSIFICATION, consent=True)
        with self.assertRaises(NeuralLODError):
            plan_lod([row("a")], True, classification=CLASSIFICATION, consent=True)

    def test_unknown_keys_and_hostile_dict_subclasses_are_rejected(self):
        bad = row("a")
        bad["extra"] = 1
        with self.assertRaises(NeuralLODError):
            plan([bad], 3)

        class HostileDict(dict):
            def keys(self):
                raise AssertionError("hostile dict method executed")

        with self.assertRaises(NeuralLODError):
            plan([HostileDict(row("a"))], 3)

    def test_duplicate_and_invalid_identifiers_are_rejected(self):
        with self.assertRaisesRegex(NeuralLODError, "unique"):
            plan([row("same"), row("same")], 10)
        with self.assertRaises(NeuralLODError):
            plan([row("../path")], 10)
        with self.assertRaises(NeuralLODError):
            plan([row("")], 10)

    def test_costs_must_be_positive_and_strictly_increasing(self):
        for costs in ((0, 1, 2), (1, 1, 2), (1, 3, 2)):
            with self.subTest(costs=costs), self.assertRaises(NeuralLODError):
                plan([row("a", costs=costs)], 10)

    def test_population_and_budget_bounds_are_enforced(self):
        with self.assertRaises(NeuralLODError):
            plan([row(str(i)) for i in range(129)], 1000)
        with self.assertRaises(NeuralLODError):
            plan([row("a")], 1_000_001)
        with self.assertRaises(NeuralLODError):
            plan([], 10)

    def test_receipt_rejects_noncanonical_population_records(self):
        result = plan([row("a")], 3)
        bad = replace(result, populations=(object(),))
        with self.assertRaises(NeuralLODError):
            verify_plan(bad)

    def test_receipt_rejects_uninitialized_forged_dataclasses(self):
        forged_plan = LODPlan.__new__(LODPlan)
        with self.assertRaisesRegex(NeuralLODError, "incomplete or forged"):
            verify_plan(forged_plan)

        result = plan([row("a")], 3)
        forged_population = PopulationLOD.__new__(PopulationLOD)
        bad = replace(result, populations=(forged_population,))
        with self.assertRaisesRegex(NeuralLODError, "incomplete or forged"):
            verify_plan(bad)


if __name__ == "__main__":
    unittest.main()
