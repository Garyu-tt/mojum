import unittest
from mojum.qa_recovery import RecoveryError, plan_recovery


def task(task_id, state="DISCOVERED", *, attempt=0, maximum=3,
         lease=None, not_before=0, dependencies=None):
    return {"task_id": task_id, "state": state, "attempt": attempt,
            "max_attempts": maximum, "lease_expires_at": lease,
            "not_before": not_before, "dependencies": dependencies or []}


class RecoveryTests(unittest.TestCase):
    def decisions(self, report):
        return {a.task_id: a.decision for a in report.actions}

    def test_expired_and_live_leases(self):
        report = plan_recovery([
            task("expired", "RUNNING", attempt=1, lease=10),
            task("live", "CLAIMED", attempt=1, lease=11),
        ], observed_at=10)
        self.assertEqual(self.decisions(report),
                         {"expired": "REQUEUE", "live": "KEEP_LEASE"})

    def test_retry_budget_exhaustion(self):
        report = plan_recovery([
            task("spent", "RUNNING", attempt=3, maximum=3, lease=4)
        ], observed_at=4)
        self.assertEqual(self.decisions(report)["spent"], "MARK_FAILED")

    def test_dependency_and_time_gates(self):
        report = plan_recovery([
            task("root", "SUCCEEDED"),
            task("child", dependencies=["root"], not_before=20),
            task("grandchild", dependencies=["child"]),
        ], observed_at=19)
        self.assertEqual(self.decisions(report), {
            "root": "KEEP_TERMINAL", "child": "DEFER", "grandchild": "DEFER"
        })

    def test_failed_dependency_blocks(self):
        report = plan_recovery([
            task("root", "FAILED"), task("child", dependencies=["root"])
        ], observed_at=100)
        self.assertEqual(self.decisions(report)["child"], "BLOCK_DEPENDENCY")

    def test_external_gates_never_auto_retry(self):
        report = plan_recovery([
            task("ci", "WAITING_CI"), task("p", "BLOCKED_PERMISSION"),
            task("c", "BLOCKED_CONFLICT")
        ], observed_at=100)
        self.assertEqual(set(self.decisions(report).values()), {"DEFER"})

    def test_deterministic_order_and_digest(self):
        a = plan_recovery([task("b"), task("a")], observed_at=5)
        b = plan_recovery([task("a"), task("b")], observed_at=5)
        self.assertEqual(a.actions, b.actions)
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(len(a.digest), 64)

    def test_rejects_cycles_unknown_dependencies_and_duplicates(self):
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a", dependencies=["b"]),
                           task("b", dependencies=["a"])], observed_at=0)
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a", dependencies=["missing"])], observed_at=0)
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a"), task("a")], observed_at=0)

    def test_rejects_bool_and_inconsistent_lease(self):
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a")], observed_at=True)
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a", "RUNNING")], observed_at=1)
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a", lease=2)], observed_at=1)

    def test_rejects_surrogate_identifiers(self):
        with self.assertRaises(RecoveryError):
            plan_recovery([task("bad" + chr(0xD800) + "id")], observed_at=0)


    def test_exhausted_retry_is_failed_before_readiness(self):
        report = plan_recovery([
            task("spent", "RETRY", attempt=3, maximum=3, not_before=0)
        ], observed_at=10)
        action = report.actions[0]
        self.assertEqual(action.decision, "MARK_FAILED")
        self.assertEqual(action.reason, "retry_budget_exhausted")

    def test_active_claim_requires_positive_attempt(self):
        for state in ("CLAIMED", "RUNNING"):
            with self.subTest(state=state):
                with self.assertRaises(RecoveryError):
                    plan_recovery([
                        task("active", state, attempt=0, lease=20)
                    ], observed_at=10)

    def test_oversized_identifier_rejected_before_utf8_encoding(self):
        with self.assertRaises(RecoveryError):
            plan_recovery([task("x" * 1_000_000)], observed_at=0)

    def test_hostile_string_subclass_field_name_rejected(self):
        class HostileKey(str):
            armed = False
            def __hash__(self):
                if self.armed:
                    raise AssertionError("hostile key was hashed")
                return 0
            def __eq__(self, other):
                if self.armed:
                    raise AssertionError("hostile key was compared")
                return str.__eq__(self, other)

        record = task("safe")
        hostile = HostileKey("task_id")
        record[hostile] = record.pop("task_id")
        hostile.armed = True
        with self.assertRaises(RecoveryError):
            plan_recovery([record], observed_at=0)

    def test_bounds_and_exact_types(self):
        with self.assertRaises(RecoveryError):
            plan_recovery([task("a")] * 501, observed_at=0)
        class HostileDict(dict):
            pass
        with self.assertRaises(RecoveryError):
            plan_recovery([HostileDict(task("a"))], observed_at=0)


if __name__ == "__main__":
    unittest.main()
