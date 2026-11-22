"""Adversarial safety checks for externally proposed stateless placements."""
import unittest
import random

from mojum.placement import (
    Assignment, NodeBudget, PlacementError, PlacementPlan, StatelessJob, plan,
)
from mojum.placement_verify import verify_placement


class PlacementVerificationTests(unittest.TestCase):
    def setUp(self):
        self.local = NodeBudget("phone", 2, 64, ("cpu",))
        self.remote = NodeBudget("server", 3, 128, ("cpu", "physics/fixture"))
        self.jobs = [StatelessJob("a", 2, 40, ("cpu",)),
                     StatelessJob("b", 2, 80, ("physics/fixture",)),
                     StatelessJob("c", 1, 32, ("cpu",))]

    def verify(self, candidate, *, trusted=frozenset(("server",)), nodes=None,
               jobs=None):
        return verify_placement(candidate, self.jobs if jobs is None else jobs,
                                [self.local, self.remote] if nodes is None else nodes,
                                local_node_id="phone", trusted_remote_ids=trusted)

    def test_planner_output_is_valid_without_executing_anything(self):
        recommended = plan(self.jobs, [self.local, self.remote],
                           local_node_id="phone",
                           trusted_remote_ids=frozenset(("server",)))
        self.assertTrue(self.verify(recommended))
        self.assertEqual({x.job_id for x in recommended.assignments}
                         | set(recommended.deferred), {"a", "b", "c"})

    def test_safe_deliberate_deferral_is_valid(self):
        self.assertTrue(self.verify(PlacementPlan((), ("c", "b", "a"))))

    def test_independent_reviewer_rejects_untrusted_remote(self):
        proposal = PlacementPlan((Assignment("a", "server"),), ("b", "c"))
        with self.assertRaisesRegex(PlacementError, "untrusted"):
            self.verify(proposal, trusted=frozenset())

    def test_rejects_unknown_or_offline_host(self):
        with self.assertRaises(PlacementError):
            self.verify(PlacementPlan((Assignment("a", "ghost"),), ("b", "c")))
        offline = NodeBudget("server", 3, 128, ("cpu", "physics/fixture"), False)
        with self.assertRaisesRegex(PlacementError, "offline"):
            self.verify(PlacementPlan((Assignment("a", "server"),), ("b", "c")),
                        nodes=[self.local, offline])

    def test_rejects_overcommit_across_individually_fitting_jobs(self):
        candidate = PlacementPlan((Assignment("a", "phone"),
                                   Assignment("c", "phone")), ("b",))
        with self.assertRaisesRegex(PlacementError, "budget"):
            self.verify(candidate)

    def test_rejects_capability_mismatch_even_if_resources_fit(self):
        with self.assertRaisesRegex(PlacementError, "capability"):
            self.verify(PlacementPlan((Assignment("b", "phone"),), ("a", "c")))

    def test_rejects_omission_and_duplicate_accounting(self):
        for candidate in (
            PlacementPlan((Assignment("a", "phone"),), ("b",)),
            PlacementPlan((Assignment("a", "phone"),), ("a", "b")),
            PlacementPlan((Assignment("a", "phone"), Assignment("a", "phone")), ("b",)),
            PlacementPlan((), ("a", "b", "unknown")),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(PlacementError):
                self.verify(candidate)

    def test_rejects_invalid_candidate_shapes_and_ids(self):
        for candidate in (
            None,
            PlacementPlan([], ["a", "b", "c"]),
            PlacementPlan(("a",), ("b", "c")),
            PlacementPlan((Assignment(1, "phone"),), ("b", "c")),
            PlacementPlan((Assignment("a", "../bad"),), ("b", "c")),
            PlacementPlan((), ("a", "b", 3)),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(PlacementError):
                self.verify(candidate)

    def test_invalid_trust_set_rejected_even_when_no_jobs_run(self):
        for trusted in (frozenset(("phone",)), frozenset(("missing",)),
                        frozenset(("../evil",)), {"server"},
                        frozenset(str(n) for n in range(65))):
            with self.subTest(trusted=str(trusted)[:80]), self.assertRaises(PlacementError):
                self.verify(PlacementPlan((), ("a", "b", "c")), trusted=trusted)

    def test_invalid_jobs_and_nodes_and_local_absence_rejected(self):
        safe = PlacementPlan((), ("a", "b", "c"))
        with self.assertRaises(PlacementError):
            self.verify(safe, jobs=self.jobs + [self.jobs[0]])
        with self.assertRaises(PlacementError):
            self.verify(safe, nodes=[self.local, self.local])
        with self.assertRaises(PlacementError):
            self.verify(safe, nodes=[self.remote])

    def test_planner_rejects_oversized_trusted_remote_inventory_early(self):
        trusted = frozenset(str(index) for index in range(64))
        with self.assertRaisesRegex(PlacementError, "invalid planner input"):
            plan(self.jobs, [self.local, self.remote], local_node_id="phone",
                 trusted_remote_ids=trusted)

    def test_maximum_admitted_63_remotes_is_still_supported(self):
        local = NodeBudget("phone", 0, 0, ("cpu",))
        remotes = [NodeBudget("peer-" + str(i), 1, 1, ("cpu",))
                   for i in range(63)]
        trust = frozenset(node.node_id for node in remotes)
        jobs = [StatelessJob("one", 1, 1, ("cpu",))]
        nodes = [local] + remotes
        proposed = plan(jobs, nodes, local_node_id="phone", trusted_remote_ids=trust)
        self.assertTrue(verify_placement(proposed, jobs, nodes,
                                         local_node_id="phone", trusted_remote_ids=trust))
        self.assertEqual(len(proposed.assignments), 1)

    def test_seeded_planner_outputs_are_accepted_across_resource_limits(self):
        rnd = random.Random(713)
        for _ in range(30):
            local = NodeBudget("phone", rnd.randrange(5), rnd.randrange(120), ("cpu",))
            remotes = [NodeBudget("peer-" + str(i), rnd.randrange(5),
                                  rnd.randrange(120), ("cpu",)) for i in range(3)]
            jobs = [StatelessJob("job-" + str(i), rnd.randint(1, 4),
                                 rnd.randint(1, 60), ("cpu",)) for i in range(20)]
            nodes = [local] + remotes
            trust = frozenset(n.node_id for n in remotes)
            proposed = plan(jobs, nodes, local_node_id="phone", trusted_remote_ids=trust)
            self.assertTrue(verify_placement(proposed, jobs, nodes,
                                             local_node_id="phone", trusted_remote_ids=trust))

    def test_zero_job_plan_is_allowed_but_never_grants_trust(self):
        self.assertTrue(self.verify(PlacementPlan((), ()), jobs=[]))
        with self.assertRaises(PlacementError):
            self.verify(PlacementPlan((Assignment("other", "phone"),), ()), jobs=[])


if __name__ == "__main__":
    unittest.main()
