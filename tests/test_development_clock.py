"""Independent no-senescence GENESIS development clock contract tests."""

import copy
import unittest

from mojum.development_clock import (
    DevelopmentError,
    MAX_COUNTER,
    POLICY_SCHEMA,
    PROJECTION_SCHEMA,
    advance,
    default_policy,
    initial_state,
    project,
    validate_policy,
    validate_state,
)


class DevelopmentClockTests(unittest.TestCase):
    def test_default_initial_state_is_zero_and_valid(self):
        state = initial_state()
        self.assertEqual(validate_state(state), state)
        self.assertEqual(project(state)["development_age_milli"], 0)
        self.assertFalse(project(state)["maturity_reached"])

    def test_development_matures_at_sixteen(self):
        state = advance(initial_state(), elapsed_ticks=160_000, development_ticks=160_000)
        projected = project(state)
        self.assertEqual(projected["schema"], PROJECTION_SCHEMA)
        self.assertEqual(projected["development_age_milli"], 16_000)
        self.assertTrue(projected["maturity_reached"])

    def test_elapsed_clock_does_not_force_growth(self):
        state = advance(initial_state(), elapsed_ticks=20_000, development_ticks=0)
        projected = project(state)
        self.assertEqual(projected["elapsed_virtual_years_milli"], 2_000)
        self.assertEqual(projected["development_age_milli"], 0)

    def test_growth_clock_can_run_independently(self):
        state = advance(initial_state(), elapsed_ticks=10, development_ticks=100_000)
        projected = project(state)
        self.assertEqual(projected["elapsed_virtual_years_milli"], 1)
        self.assertEqual(projected["development_age_milli"], 10_000)

    def test_maturity_clamps_but_lifetime_and_learning_continue(self):
        mature = advance(initial_state(), elapsed_ticks=160_000, development_ticks=160_000)
        later = advance(mature, elapsed_ticks=1_000_000, development_ticks=1_000_000, learning_events=234)
        first = project(mature)
        last = project(later)
        self.assertEqual(first["development_age_milli"], last["development_age_milli"])
        self.assertGreater(last["elapsed_virtual_years_milli"], first["elapsed_virtual_years_milli"])
        self.assertEqual(last["learning_events"], 234)
        self.assertFalse(last["senescence_enabled"])
        self.assertFalse(last["age_related_death_enabled"])

    def test_custom_start_at_six_years_and_ceiling_sixteen(self):
        policy = default_policy()
        policy.update(start_age_milli=6_000, maturity_age_milli=16_000, ticks_per_virtual_year=10)
        state = advance(initial_state(), development_ticks=100, elapsed_ticks=25, learning_events=3)
        observed = project(state, policy)
        self.assertEqual(observed["development_age_milli"], 16_000)
        self.assertEqual(observed["elapsed_virtual_years_milli"], 2_500)
        self.assertEqual(observed["learning_events"], 3)

    def test_already_mature_at_initial_tick_stays_mature_without_age_decay(self):
        policy = default_policy()
        policy.update(start_age_milli=16_000, maturity_age_milli=16_000)
        before = project(initial_state(), policy)
        self.assertTrue(before["maturity_reached"])
        self.assertEqual(before["development_age_milli"], 16_000)
        self.assertEqual(before["elapsed_virtual_years_milli"], 0)
        later = advance(initial_state(), elapsed_ticks=100_000, development_ticks=0, learning_events=42)
        after = project(later, policy)
        self.assertEqual(after["development_age_milli"], 16_000)
        self.assertTrue(after["maturity_reached"])
        self.assertEqual(after["learning_events"], 42)
        self.assertFalse(after["senescence_enabled"])
        self.assertFalse(after["age_related_death_enabled"])

    def test_learning_counter_does_not_force_growth_or_age(self):
        state = advance(initial_state(), elapsed_ticks=0, development_ticks=0, learning_events=500)
        observed = project(state)
        self.assertEqual(observed["learning_events"], 500)
        self.assertEqual(observed["development_age_milli"], 0)
        self.assertEqual(observed["elapsed_virtual_years_milli"], 0)

    def test_contract_rejects_enabled_senescence_and_wrong_boolean_type(self):
        for value in (True, 0, 1, "false", None):
            p = default_policy()
            p["senescence_enabled"] = value
            with self.subTest(value=value), self.assertRaises(DevelopmentError):
                validate_policy(p)

    def test_policy_fails_closed_for_invalid_numbers_or_unknown_keys(self):
        for field, value in (("ticks_per_virtual_year", 0), ("ticks_per_virtual_year", True),
                             ("maturity_age_milli", 2.5), ("start_age_milli", -1)):
            p = default_policy()
            p[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(DevelopmentError):
                project(initial_state(), p)
        p = default_policy()
        p["start_age_milli"] = 20_000
        with self.assertRaises(DevelopmentError):
            validate_policy(p)
        p = default_policy()
        p["unexpected"] = "eval('never')"
        with self.assertRaises(DevelopmentError):
            validate_policy(p)
        p = default_policy()
        p["schema"] = POLICY_SCHEMA + "/2"
        with self.assertRaises(DevelopmentError):
            validate_policy(p)

    def test_state_fails_closed_for_invalid_fields_or_schema(self):
        for field, value in (("elapsed_ticks", True), ("development_ticks", -2),
                             ("learning_events", 1.0), ("elapsed_ticks", MAX_COUNTER + 1)):
            s = initial_state()
            s[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(DevelopmentError):
                project(s)
        s = initial_state()
        s["unknown"] = 1
        with self.assertRaises(DevelopmentError):
            advance(s)
        s = initial_state()
        s["schema"] = "unknown"
        with self.assertRaises(DevelopmentError):
            validate_state(s)

    def test_overflow_and_invalid_increments_leave_state_untouched(self):
        s = initial_state()
        s["elapsed_ticks"] = MAX_COUNTER
        before = copy.deepcopy(s)
        for args in ({"elapsed_ticks": 1}, {"development_ticks": float("nan")},
                     {"learning_events": True}, {"development_ticks": -1}):
            with self.subTest(args=args), self.assertRaises(DevelopmentError):
                advance(s, **args)
            self.assertEqual(s, before)

    def test_no_mutation_of_state_or_policy(self):
        s = initial_state()
        p = default_policy()
        s_before, p_before = copy.deepcopy(s), copy.deepcopy(p)
        updated = advance(s, elapsed_ticks=12, development_ticks=5, learning_events=2)
        project(updated, p)
        self.assertEqual(s, s_before)
        self.assertEqual(p, p_before)
        self.assertEqual(updated["elapsed_ticks"], 12)
        self.assertIsNot(s, updated)
        self.assertIsNot(default_policy(), default_policy())

    def test_replay_same_inputs_same_projection(self):
        steps = ((1, 0, 2), (42, 100, 0), (120, 80, 7), (0, 0, 25))
        results = []
        for _ in range(2):
            state = initial_state()
            for lived, growth, learned in steps:
                state = advance(state, elapsed_ticks=lived, development_ticks=growth, learning_events=learned)
            results.append(project(state))
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
