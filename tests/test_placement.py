"""Offline local-first placement fixture: it never starts remote work."""
import unittest
import random

from mojum.placement import (
    Assignment, NodeBudget, PlacementError, PlacementPlan, StatelessJob, plan,
)


def local(cpu=2, memory=64):
    return NodeBudget("phone-1", cpu, memory, ("cpu", "vision/fixture"))


def remote(cpu=4, memory=256, *, online=True):
    return NodeBudget("server-1", cpu, memory, ("cpu", "physics/fixture"), online)


class LocalFirstPlacementTests(unittest.TestCase):
    def test_seeded_many_jobs_never_exceed_any_budget(self):
        rnd = random.Random(2104)
        for iteration in range(40):
            nodes = [local(cpu=rnd.randint(0, 8), memory=rnd.randint(0, 320))]
            nodes += [NodeBudget("remote-" + str(i), rnd.randint(0, 8),
                                 rnd.randint(0, 320), ("cpu",)) for i in range(3)]
            jobs = [StatelessJob("job-" + str(i), rnd.randint(1, 4),
                                 rnd.randint(1, 160), ("cpu",)) for i in range(35)]
            result = plan(jobs, nodes, local_node_id="phone-1",
                          trusted_remote_ids=frozenset(n.node_id for n in nodes[1:]))
            allocations = {n.node_id: [0, 0] for n in nodes}
            specs = {j.job_id: j for j in jobs}
            all_ids = set()
            for assignment in result.assignments:
                self.assertNotIn(assignment.job_id, all_ids)
                all_ids.add(assignment.job_id)
                allocations[assignment.node_id][0] += specs[assignment.job_id].cpu_slots
                allocations[assignment.node_id][1] += specs[assignment.job_id].memory_mib
            self.assertEqual(all_ids | set(result.deferred), set(specs))
            self.assertEqual(len(all_ids) + len(result.deferred), len(specs))
            for node in nodes:
                with self.subTest(iteration=iteration, node=node.node_id):
                    self.assertLessEqual(allocations[node.node_id][0], node.cpu_slots)
                    self.assertLessEqual(allocations[node.node_id][1], node.memory_mib)

    def test_bounded_rejects_oversized_job_queue(self):
        jobs = [StatelessJob("job-" + str(i), 1, 1) for i in range(257)]
        with self.assertRaises(PlacementError):
            plan(jobs, [local()], local_node_id="phone-1")

    def test_standalone_offline_node_needs_no_mesh(self):
        result = plan([StatelessJob("job-1", 1, 16, ("cpu",))],
                      [local()], local_node_id="phone-1")
        self.assertEqual(result, PlacementPlan((Assignment("job-1", "phone-1"),), ()))

    def test_unverified_remotes_are_never_used(self):
        result = plan([StatelessJob("job-1", 3, 16, ("cpu",))],
                      [local(), remote()], local_node_id="phone-1")
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.deferred, ("job-1",))

    def test_explicitly_admitted_remote_can_take_overflow_only(self):
        jobs = [StatelessJob("a", 2, 60, ("cpu",)),
                StatelessJob("b", 3, 16, ("cpu",))]
        result = plan(jobs, [remote(), local()], local_node_id="phone-1",
                      trusted_remote_ids=frozenset(("server-1",)))
        self.assertEqual(result.assignments, (
            Assignment("a", "phone-1"), Assignment("b", "server-1")))

    def test_offline_remote_does_not_accept_work(self):
        result = plan([StatelessJob("a", 3, 16)], [local(), remote(online=False)],
                      local_node_id="phone-1",
                      trusted_remote_ids=frozenset(("server-1",)))
        self.assertEqual(result.deferred, ("a",))

    def test_capability_gate_applies_to_local_and_remote(self):
        job = StatelessJob("physics", 1, 16, ("physics/fixture",))
        result = plan([job], [local(), remote()], local_node_id="phone-1",
                      trusted_remote_ids=frozenset(("server-1",)))
        self.assertEqual(result.assignments, (Assignment("physics", "server-1"),))
        self.assertEqual(plan([job], [local()], local_node_id="phone-1").deferred,
                         ("physics",))

    def test_no_cpu_or_memory_overcommit(self):
        jobs = [StatelessJob(str(i), 1, 40) for i in range(4)]
        result = plan(jobs, [local(cpu=2, memory=64)], local_node_id="phone-1")
        self.assertEqual(len(result.assignments), 1)
        self.assertEqual(len(result.deferred), 3)

    def test_priority_and_ids_make_input_order_irrelevant(self):
        a = StatelessJob("z", 1, 16, priority=0)
        b = StatelessJob("a", 1, 16, priority=9)
        c = StatelessJob("b", 1, 16, priority=9)
        one = plan([a, b, c], [local(cpu=2)], local_node_id="phone-1")
        two = plan([c, a, b], [local(cpu=2)], local_node_id="phone-1")
        self.assertEqual(one, two)
        self.assertEqual(one.assignments, (
            Assignment("a", "phone-1"), Assignment("b", "phone-1")))
        self.assertEqual(one.deferred, ("z",))

    def test_input_budgets_not_modified(self):
        hosts = [local(), remote()]
        jobs = [StatelessJob("a", 1, 16), StatelessJob("b", 2, 128)]
        copy = tuple(hosts), tuple(jobs)
        plan(jobs, hosts, local_node_id="phone-1",
             trusted_remote_ids=frozenset(("server-1",)))
        self.assertEqual((tuple(hosts), tuple(jobs)), copy)

    def test_duplicate_jobs_and_peers_rejected(self):
        with self.assertRaises(PlacementError):
            plan([StatelessJob("dup", 1, 1)] * 2,
                 [local()], local_node_id="phone-1")
        with self.assertRaises(PlacementError):
            plan([], [local(), local()], local_node_id="phone-1")

    def test_invalid_ids_and_resources_rejected(self):
        for name in ("", "../node", "a\x00b", " node ", "n" * 129):
            with self.subTest(name=name), self.assertRaises(PlacementError):
                NodeBudget(name, 1, 1, ("cpu",))
        with self.assertRaises(PlacementError):
            NodeBudget("n", True, 1, ())
        with self.assertRaises(PlacementError):
            StatelessJob("job", 1, -1)
        with self.assertRaises(PlacementError):
            StatelessJob("job", 1, 1, ("cpu//fixture",))
        with self.assertRaises(PlacementError):
            StatelessJob("job", 1, 1, priority=True)
        with self.assertRaises(PlacementError):
            NodeBudget("n", 1, 1, ("cpu",), online=1)

    def test_stateful_jobs_fail_closed(self):
        with self.assertRaisesRegex(PlacementError, "stateful"):
            StatelessJob("brain-state", 1, 1, stateless=False)
        with self.assertRaisesRegex(PlacementError, "stateful"):
            StatelessJob("world", 1, 1, stateless=1)

    def test_local_and_unknown_peer_admission_rejected(self):
        with self.assertRaises(PlacementError):
            plan([], [local()], local_node_id="phone-1",
                 trusted_remote_ids=frozenset(("phone-1",)))
        with self.assertRaises(PlacementError):
            plan([], [local()], local_node_id="phone-1",
                 trusted_remote_ids=frozenset(("ghost",)))
        with self.assertRaises(PlacementError):
            plan([], [local()], local_node_id="phone-1",
                 trusted_remote_ids={"server-1"})

    def test_local_runtime_is_always_required_but_not_centralized(self):
        with self.assertRaises(PlacementError):
            plan([], [remote()], local_node_id="phone-1")
        with self.assertRaises(PlacementError):
            plan([], [NodeBudget("phone-1", 2, 64, (), online=False)],
                 local_node_id="phone-1")


if __name__ == "__main__":
    unittest.main()
