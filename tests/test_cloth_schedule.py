import unittest
from mojum.cloth_schedule import ClothSchedulePolicy, PROFILES, SCHEMA, plan_step, policy_digest

class ClothScheduleTests(unittest.TestCase):
    def test_profiles_and_default_off(self):
        self.assertFalse(PROFILES["OFF"].enabled)
        self.assertEqual((PROFILES["LOW"].tick_divisor, PROFILES["MEDIUM"].tick_divisor, PROFILES["HIGH"].tick_divisor), (4, 2, 1))

    def test_logical_tick_cadence(self):
        self.assertFalse(plan_step(PROFILES["LOW"], 3).run)
        self.assertEqual(plan_step(PROFILES["LOW"], 4).reason, "run")

    def test_work_budget_fails_closed(self):
        result = plan_step(PROFILES["LOW"], 4, requested_vertices=2001)
        self.assertFalse(result.run)
        self.assertEqual(result.reason, "work-budget-exceeded")

    def test_backlog_is_bounded_and_explicit(self):
        result = plan_step(PROFILES["LOW"], 4, pending_steps=5)
        self.assertTrue(result.run)
        self.assertEqual(result.dropped_pending_steps, 4)
        self.assertEqual(result.reason, "run-with-backlog-dropped")

    def test_disable(self):
        self.assertEqual(plan_step(PROFILES["HIGH"], 1, enabled=False).reason, "disabled")
        self.assertFalse(plan_step(PROFILES["OFF"], 1).run)

    def test_digest_stable_and_sensitive(self):
        p = PROFILES["LOW"]
        self.assertEqual(policy_digest(p), policy_digest(p))
        other = ClothSchedulePolicy("CUSTOM", 4, 1, 1999, 8000)
        self.assertNotEqual(policy_digest(p), policy_digest(other))

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

if __name__ == "__main__":
    unittest.main()
