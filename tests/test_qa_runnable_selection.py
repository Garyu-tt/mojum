import unittest

from mojum.qa_runnable_selection import SelectionError, select_runnable_tasks


class RunnableSelectionTests(unittest.TestCase):
    def test_blocked_task_does_not_block_independent_work(self):
        result = select_runnable_tasks([
            {"task_id": "forbidden", "state": "BLOCKED_PERMISSION", "priority": 99, "sequence": 1},
            {"task_id": "pending", "state": "WAITING_CI", "priority": 98, "sequence": 2},
            {"task_id": "ready-a", "state": "DISCOVERED", "priority": 2, "sequence": 3},
            {"task_id": "ready-b", "state": "RETRY", "priority": 1, "sequence": 4},
        ], now_tick=10, limit=10)
        self.assertEqual(result.selected, ("ready-a", "ready-b"))
        self.assertEqual(result.deferred, (
            ("forbidden", "BLOCKED_PERMISSION"),
            ("pending", "WAITING_CI"),
        ))

    def test_priority_then_fifo_then_id_is_deterministic(self):
        tasks = [
            {"task_id": "z", "state": "DISCOVERED", "priority": 5, "sequence": 2},
            {"task_id": "b", "state": "DISCOVERED", "priority": 5, "sequence": 1},
            {"task_id": "a", "state": "DISCOVERED", "priority": 5, "sequence": 1},
            {"task_id": "low", "state": "DISCOVERED", "priority": 1, "sequence": 0},
        ]
        first = select_runnable_tasks(tasks, now_tick=0, limit=10)
        second = select_runnable_tasks(list(reversed(tasks)), now_tick=0, limit=10)
        self.assertEqual(first, second)
        self.assertEqual(first.selected, ("a", "b", "z", "low"))

    def test_not_before_defers_until_logical_tick(self):
        task = {"task_id": "later", "state": "RETRY", "priority": 1,
                "sequence": 0, "not_before": 11}
        early = select_runnable_tasks([task], now_tick=10)
        due = select_runnable_tasks([task], now_tick=11)
        self.assertEqual(early.selected, ())
        self.assertEqual(early.deferred, (("later", "NOT_DUE"),))
        self.assertEqual(due.selected, ("later",))

    def test_dependencies_require_merged_parent(self):
        tasks = [
            {"task_id": "child", "state": "DISCOVERED", "priority": 10,
             "sequence": 0, "dependencies": ["parent"]},
            {"task_id": "parent", "state": "WAITING_CI", "priority": 0, "sequence": 1},
            {"task_id": "independent", "state": "DISCOVERED", "priority": 1, "sequence": 2},
        ]
        result = select_runnable_tasks(tasks, now_tick=0)
        self.assertEqual(result.selected, ("independent",))
        self.assertIn(("child", "DEPENDENCY_NOT_MERGED"), result.deferred)

    def test_missing_dependency_is_fail_closed_but_nonblocking(self):
        tasks = [
            {"task_id": "orphan", "state": "DISCOVERED", "priority": 10,
             "sequence": 0, "dependencies": ["missing"]},
            {"task_id": "ok", "state": "DISCOVERED", "priority": 1, "sequence": 1},
        ]
        result = select_runnable_tasks(tasks, now_tick=0)
        self.assertEqual(result.selected, ("ok",))
        self.assertEqual(result.deferred, (("orphan", "DEPENDENCY_NOT_MERGED"),))

    def test_active_and_terminal_items_are_ignored(self):
        states = ["CLAIMED", "REVIEWING", "TESTING", "FIXING", "RETESTING",
                  "READY", "MERGED", "CLOSED", "CANCELLED"]
        tasks = [{"task_id": state, "state": state, "priority": 1, "sequence": i}
                 for i, state in enumerate(states)]
        result = select_runnable_tasks(tasks, now_tick=0)
        self.assertEqual(result.selected, ())
        self.assertEqual(len(result.ignored), len(states))

    def test_escalation_and_conflict_do_not_head_of_line_block(self):
        tasks = [
            {"task_id": "escalated", "state": "ESCALATED", "priority": 100, "sequence": 0},
            {"task_id": "conflict", "state": "BLOCKED_CONFLICT", "priority": 90, "sequence": 1},
            {"task_id": "ordinary", "state": "DISCOVERED", "priority": 1, "sequence": 2},
        ]
        self.assertEqual(select_runnable_tasks(tasks, now_tick=0).selected, ("ordinary",))

    def test_batch_limit_is_reported(self):
        tasks = [{"task_id": str(i), "state": "DISCOVERED", "priority": 0, "sequence": i}
                 for i in range(3)]
        result = select_runnable_tasks(tasks, now_tick=0, limit=2)
        self.assertEqual(result.selected, ("0", "1"))
        self.assertEqual(result.deferred, (("2", "BATCH_LIMIT"),))

    def test_duplicate_ids_are_rejected(self):
        item = {"task_id": "same", "state": "DISCOVERED", "priority": 0, "sequence": 0}
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item, dict(item)], now_tick=0)

    def test_unknown_fields_and_states_are_rejected(self):
        base = {"task_id": "x", "state": "DISCOVERED", "priority": 0, "sequence": 0}
        with self.assertRaises(SelectionError):
            select_runnable_tasks([dict(base, token="must-not-be-accepted")], now_tick=0)
        with self.assertRaises(SelectionError):
            select_runnable_tasks([dict(base, state="FORBIDDEN")], now_tick=0)

    def test_bool_is_not_an_integer(self):
        item = {"task_id": "x", "state": "DISCOVERED", "priority": True, "sequence": 0}
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item], now_tick=0)

    def test_exact_native_container_types_are_required(self):
        class Task(dict):
            pass
        with self.assertRaises(SelectionError):
            select_runnable_tasks([Task(task_id="x", state="DISCOVERED",
                                        priority=0, sequence=0)], now_tick=0)
        with self.assertRaises(SelectionError):
            select_runnable_tasks((), now_tick=0)

    def test_hostile_string_subclass_key_is_rejected_before_hash_or_equality(self):
        class HostileKey(str):
            armed = False

            def __hash__(self):
                if self.armed:
                    raise AssertionError("hostile __hash__ executed")
                return str.__hash__(self)

            def __eq__(self, other):
                if self.armed:
                    raise AssertionError("hostile __eq__ executed")
                return str.__eq__(self, other)

        key = HostileKey("task_id")
        item = {key: "x", "state": "DISCOVERED", "priority": 0, "sequence": 0}
        key.armed = True
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item], now_tick=0)

    def test_oversized_field_name_is_rejected(self):
        item = {"task_id": "x", "state": "DISCOVERED", "priority": 0,
                "sequence": 0, "x" * 100_000: None}
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item], now_tick=0)

    def test_self_and_duplicate_dependencies_are_rejected(self):
        item = {"task_id": "x", "state": "DISCOVERED", "priority": 0,
                "sequence": 0, "dependencies": ["x"]}
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item], now_tick=0)
        item["dependencies"] = ["p", "p"]
        with self.assertRaises(SelectionError):
            select_runnable_tasks([item], now_tick=0)

    def test_input_is_not_mutated(self):
        item = {"task_id": "x", "state": "DISCOVERED", "priority": 1,
                "sequence": 0, "dependencies": []}
        before = dict(item)
        select_runnable_tasks([item], now_tick=0)
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
