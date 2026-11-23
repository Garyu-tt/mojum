"""Regression: the public mesh admission gate cannot trust frozen input objects.

stdlib only, synthetic/offline; no node enrollment, resource allocation or execution.
"""
import unittest

from mojum.placement import (
    Assignment, NodeBudget, PlacementError, PlacementPlan, StatelessJob, plan,
)
from mojum.placement_verify import verify_placement


class _SubclassedString(str):
    pass


class PlacementAdmissionRecheckTests(unittest.TestCase):
    def setUp(self):
        self.node = NodeBudget("local-1", 2, 64, ("cpu",))
        self.job = StatelessJob("job-1", 1, 16, ("cpu",))
        self.candidate = PlacementPlan((Assignment("job-1", "local-1"),), ())

    def assert_both_reject(self, *, node=None, job=None):
        node = self.node if node is None else node
        job = self.job if job is None else job
        with self.assertRaises(PlacementError):
            plan([job], [node], local_node_id="local-1")
        with self.assertRaises(PlacementError):
            verify_placement(self.candidate, [job], [node],
                             local_node_id="local-1")

    def test_normal_local_first_plan_and_independent_verification(self):
        result = plan([self.job], [self.node], local_node_id="local-1")
        self.assertEqual(result, self.candidate)
        self.assertTrue(verify_placement(result, [self.job], [self.node],
                                         local_node_id="local-1"))

    def test_post_construction_node_mutations_rejected_at_both_boundaries(self):
        cases = (
            ("cpu_slots", -5),
            ("cpu_slots", True),
            ("memory_mib", -1),
            ("capabilities", ("cpu", "root/../device")),
            ("capabilities", ["cpu"]),
            ("capabilities", (_SubclassedString("cpu"),)),
            ("node_id", "../local-1"),
            ("online", 1),
        )
        for field, bad_value in cases:
            with self.subTest(field=field, value=repr(bad_value)):
                node = NodeBudget("local-1", 2, 64, ("cpu",))
                object.__setattr__(node, field, bad_value)
                self.assert_both_reject(node=node)

    def test_post_construction_job_mutations_rejected_at_both_boundaries(self):
        cases = (
            ("cpu_slots", -5),
            ("cpu_slots", False),
            ("memory_mib", -3),
            ("priority", True),
            ("stateless", False),
            ("stateless", 1),
            ("required_capabilities", ["cpu"]),
            ("required_capabilities", (_SubclassedString("cpu"),)),
            ("job_id", "bad/id"),
        )
        for field, bad_value in cases:
            with self.subTest(field=field, value=repr(bad_value)):
                job = StatelessJob("job-1", 1, 16, ("cpu",))
                object.__setattr__(job, field, bad_value)
                self.assert_both_reject(job=job)

    def test_uninitialized_records_fail_with_placement_error_not_attribute_error(self):
        self.assert_both_reject(node=object.__new__(NodeBudget))
        self.assert_both_reject(job=object.__new__(StatelessJob))

    def test_valid_inputs_remain_intact_after_snapshot(self):
        node_before = (self.node.node_id, self.node.cpu_slots,
                       self.node.memory_mib, self.node.capabilities,
                       self.node.online)
        job_before = (self.job.job_id, self.job.cpu_slots, self.job.memory_mib,
                      self.job.required_capabilities, self.job.priority,
                      self.job.stateless)
        plan([self.job], [self.node], local_node_id="local-1")
        verify_placement(self.candidate, [self.job], [self.node],
                         local_node_id="local-1")
        self.assertEqual(node_before, (self.node.node_id, self.node.cpu_slots,
                                      self.node.memory_mib, self.node.capabilities,
                                      self.node.online))
        self.assertEqual(job_before, (self.job.job_id, self.job.cpu_slots,
                                     self.job.memory_mib, self.job.required_capabilities,
                                     self.job.priority, self.job.stateless))


if __name__ == "__main__":
    unittest.main()
