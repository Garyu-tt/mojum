import unittest
from mojum.cloth_schedule import ClothSchedulePolicy, PROFILES, SCHEMA, plan_step, policy_digest

class ClothScheduleTests(unittest.TestCase):
    def test_profiles_and_default_off(self):
        self.assertFalse(PROFILES["OFF"].enabled)
        self.assertEqual((PROFILES["LOW"].tick_divisor, PROFILES["MEDIUM"].tick_divisor, PROFILES["HIGH"].tick_divisor), (4, 2, 1))

    def test_logical_tick_cadence(self):
        result = plan_step(PROFILES["LOW"], 3, pending_steps=2)
        self.assertFalse(result.run)
        self.assertEqual(result.reason, "cadence")
        self.assertEqual((result.admitted_steps, result.deferred_pending_steps, result.dropped_pending_steps), (0, 2, 0))
        self.assertEqual(plan_step(PROFILES["LOW"], 4).reason, "run")

    def test_work_budget_fails_closed(self):
        result = plan_step(PROFILES["LOW"], 4, requested_vertices=2001)
        self.assertFalse(result.run)
        self.assertEqual(result.reason, "work-budget-exceeded")
        self.assertEqual(result.deferred_pending_steps, 1)
        self.assertEqual(result.dropped_pending_steps, 0)

    def test_backlog_is_bounded_and_explicit(self):
        result = plan_step(PROFILES["LOW"], 4, pending_steps=5)
        self.assertTrue(result.run)
        self.assertEqual(result.admitted_steps, 1)
        self.assertEqual(result.deferred_pending_steps, 0)
        self.assertEqual(result.dropped_pending_steps, 4)
        self.assertEqual(result.reason, "run-with-backlog-dropped")

    def test_catch_up_boundaries_for_all_profiles(self):
        for profile, cap in ((PROFILES["LOW"], 1), (PROFILES["MEDIUM"], 1), (PROFILES["HIGH"], 2)):
            for pending, expected_admitted in ((1, 1), (2, min(2, cap)), (3, min(3, cap))):
                with self.subTest(profile=profile.profile, pending=pending):
                    result = plan_step(profile, profile.tick_divisor, pending_steps=pending)
                    self.assertEqual(result.admitted_steps, expected_admitted)
                    self.assertEqual(result.dropped_pending_steps, pending - expected_admitted)
                    self.assertEqual(result.deferred_pending_steps, 0)
                    self.assertEqual(result.run, expected_admitted > 0)

    def test_disable_and_cadence_defer_without_losing_work(self):
        for result in (plan_step(PROFILES["HIGH"], 1, pending_steps=3, enabled=False),
                       plan_step(PROFILES["OFF"], 1, pending_steps=3)):
            self.assertEqual(result.reason, "disabled")
            self.assertEqual(result.deferred_pending_steps, 3)
            self.assertEqual(result.dropped_pending_steps, 0)

    def test_digest_stable_and_sensitive(self):
        p = PROFILES["LOW"]
        self.assertEqual(policy_digest(p), policy_digest(p))
        other = ClothSchedulePolicy("CUSTOM", 4, 1, 1999, 8000)
        self.assertNotEqual(policy_digest(p), policy_digest(other))

    def test_enabled_policy_cannot_have_zero_capacity(self):
        with self.assertRaisesRegex(ValueError, "at least one catch-up step"):
            ClothSchedulePolicy("CUSTOM", 1, 0, 100, 100, True)
        self.assertEqual(ClothSchedulePolicy("CUSTOM", 1, 0, 0, 0, False).max_catch_up_steps, 0)

    def test_mutated_or_subclassed_policy_is_rejected(self):
        policy = ClothSchedulePolicy("LOW", 4, 1, 2000, 8000)
        object.__setattr__(policy, "max_catch_up_steps", 0)
        with self.assertRaises(ValueError):
            policy_digest(policy)
        with self.assertRaises(ValueError):
            plan_step(policy, 4)
        class PolicySubclass(ClothSchedulePolicy):
            pass
        subclassed = PolicySubclass("CUSTOM", 1, 1, 10, 10)
        with self.assertRaises(TypeError):
            plan_step(subclassed, 1)

    def test_strict_validation(self):
        for kwargs in ({"logical_tick": True}, {"logical_tick": 1, "pending_steps": False},
                       {"logical_tick": 1, "requested_vertices": True},
                       {"logical_tick": 1, "requested_constraints": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plan_step(PROFILES["LOW"], **kwargs)
        with self.assertRaises(ValueError):
            ClothSchedulePolicy("LOW", 0, 1, 100, 100)
        with self.assertRaises(ValueError):
            ClothSchedulePolicy("LOW", 1, 9, 100, 100)

    def test_plan_is_detached(self):
        p = PROFILES["MEDIUM"]
        before = policy_digest(p)
        result = plan_step(p, 2, requested_vertices=100)
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(policy_digest(p), before)
        self.assertEqual(result.admitted_vertices, 100)
        self.assertEqual(result.admitted_steps, 1)

if __name__ == "__main__":
    unittest.main()
