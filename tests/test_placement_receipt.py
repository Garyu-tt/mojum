"""Hermetic adversarial acceptance tests for logical-epoch placement receipts."""
import dataclasses
import json
import unittest

from mojum.placement import (
    Assignment, NodeBudget, PlacementError, PlacementPlan, StatelessJob,
)
from mojum.placement_receipt import (
    MAX_WIRE_BYTES, PlacementReceipt, SCHEMA, parse_receipt, receipt_bytes,
    seal_placement, verify_receipt,
)


class PlacementReceiptTests(unittest.TestCase):
    def setUp(self):
        self.jobs = (
            StatelessJob("a", 2, 20, ("cpu",), priority=2),
            StatelessJob("b", 1, 10, ("gpu",)),
        )
        self.nodes = (
            NodeBudget("phone", 2, 32, ("cpu",)),
            NodeBudget("server", 2, 24, ("cpu", "gpu")),
        )
        self.trust = frozenset({"server"})
        self.plan = PlacementPlan(
            (Assignment("a", "phone"), Assignment("b", "server")), ()
        )

    def seal(self, *, plan=None, jobs=None, nodes=None, trust=None, epoch=7):
        return seal_placement(
            self.plan if plan is None else plan,
            self.jobs if jobs is None else jobs,
            self.nodes if nodes is None else nodes,
            local_node_id="phone",
            trusted_remote_ids=self.trust if trust is None else trust,
            epoch=epoch,
        )

    def verify(self, receipt, *, plan=None, jobs=None, nodes=None,
               trust=None, epoch=7):
        return verify_receipt(
            receipt,
            self.plan if plan is None else plan,
            self.jobs if jobs is None else jobs,
            self.nodes if nodes is None else nodes,
            local_node_id="phone",
            trusted_remote_ids=self.trust if trust is None else trust,
            expected_epoch=epoch,
        )

    def test_valid_roundtrip_and_independent_reverification(self):
        receipt = self.seal()
        self.assertEqual(receipt.schema, SCHEMA)
        self.assertEqual(receipt.epoch, 7)
        wire = receipt_bytes(receipt)
        self.assertEqual(parse_receipt(wire), receipt)
        self.assertEqual(receipt_bytes(parse_receipt(wire)), wire)
        self.assertTrue(self.verify(parse_receipt(wire)))
        self.assertLess(len(wire), MAX_WIRE_BYTES)

    def test_inventory_and_plan_order_do_not_change_fingerprint(self):
        alt_nodes = (
            NodeBudget("server", 2, 24, ("gpu", "cpu")),
            self.nodes[0],
        )
        alt_plan = PlacementPlan(tuple(reversed(self.plan.assignments)), ())
        self.assertEqual(
            self.seal(),
            self.seal(plan=alt_plan, jobs=tuple(reversed(self.jobs)),
                      nodes=alt_nodes),
        )
        self.assertTrue(self.verify(self.seal(), plan=alt_plan,
                                    jobs=tuple(reversed(self.jobs)),
                                    nodes=alt_nodes))

    def test_epoch_changes_fail_closed_even_if_everything_else_matches(self):
        receipt = self.seal(epoch=7)
        for epoch in (0, 6, 8, 2**63 - 1):
            with self.subTest(epoch=epoch), self.assertRaises(PlacementError):
                self.verify(receipt, epoch=epoch)
        for epoch in (True, 1.0, -1, 2**63, "7"):
            with self.subTest(epoch=epoch), self.assertRaises(PlacementError):
                self.seal(epoch=epoch)

    def test_safe_but_different_plan_is_not_same_receipt(self):
        other = PlacementPlan((Assignment("a", "phone"),), ("b",))
        with self.assertRaisesRegex(PlacementError, "mismatched"):
            self.verify(self.seal(), plan=other)
        self.assertNotEqual(self.seal(), self.seal(plan=other))

    def test_reordered_same_assignments_ok_but_duplicate_rejected(self):
        bad = PlacementPlan((Assignment("a", "phone"), Assignment("a", "phone")),
                            ())
        with self.assertRaises(PlacementError):
            self.seal(plan=bad)

    def test_resource_or_capability_inventory_changes_rejected(self):
        variants = (
            (self.nodes[0], NodeBudget("server", 3, 24, ("cpu", "gpu"))),
            (self.nodes[0], NodeBudget("server", 2, 24, ("cpu", "gpu", "audio"))),
            (NodeBudget("phone", 3, 32, ("cpu",)), self.nodes[1]),
        )
        for nodes in variants:
            with self.subTest(nodes=nodes), self.assertRaisesRegex(
                    PlacementError, "mismatched"):
                self.verify(self.seal(), nodes=nodes)

    def test_job_and_admission_changes_rejected(self):
        jobs = (StatelessJob("a", 2, 20, ("cpu",), priority=3), self.jobs[1])
        with self.assertRaisesRegex(PlacementError, "mismatched"):
            self.verify(self.seal(), jobs=jobs)
        with self.assertRaises(PlacementError):
            self.verify(self.seal(), trust=frozenset())
        with self.assertRaises(PlacementError):
            self.seal(nodes=(self.nodes[0], NodeBudget("server", 2, 24,
                            ("cpu", "gpu"), online=False)))

    def test_zero_job_plan_can_be_sealed_without_granting_remote_trust(self):
        empty = PlacementPlan((), ())
        receipt = self.seal(plan=empty, jobs=(), trust=frozenset())
        self.assertTrue(self.verify(receipt, plan=empty, jobs=(),
                                    trust=frozenset()))

    def test_rejects_forged_unvalidated_budget_records(self):
        forged = object.__new__(NodeBudget)
        object.__setattr__(forged, "node_id", "server")
        object.__setattr__(forged, "cpu_slots", True)
        object.__setattr__(forged, "memory_mib", 24)
        object.__setattr__(forged, "capabilities", ("cpu", "gpu"))
        object.__setattr__(forged, "online", True)
        with self.assertRaises(PlacementError):
            self.seal(nodes=(self.nodes[0], forged))

    def test_immutable_receipt_and_mismatched_or_forged_digest(self):
        original = self.seal()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            original.epoch = 8
        fake = dataclasses.replace(original, receipt_sha256="0" * 64)
        with self.assertRaisesRegex(PlacementError, "mismatched"):
            self.verify(fake)
        with self.assertRaises(PlacementError):
            self.verify(None)
        with self.assertRaises(PlacementError):
            receipt_bytes(None)

    def test_forged_receipt_cannot_execute_deepcopy_before_validation(self):
        class Hostile:
            def __deepcopy__(self, memo):
                raise RuntimeError("untrusted deepcopy executed")

        valid = dataclasses.asdict(self.seal())
        for field in valid:
            with self.subTest(field=field):
                forged = object.__new__(PlacementReceipt)
                for key, value in valid.items():
                    object.__setattr__(
                        forged, key, Hostile() if key == field else value,
                    )
                with self.assertRaises(PlacementError):
                    receipt_bytes(forged)
                with self.assertRaises(PlacementError):
                    self.verify(forged)

        missing = object.__new__(PlacementReceipt)
        with self.assertRaises(PlacementError):
            receipt_bytes(missing)

    def test_wire_duplicate_extra_and_malformed_fields_rejected(self):
        wire = receipt_bytes(self.seal())
        dictionary = json.loads(wire)
        variants = (
            b'{"schema":1,"schema":2}',
            b'{"schema": NaN}',
            b'{"schema": null}',
            b'[]',
            b'null',
            b'\xff',
            b'{}' + b' ' * MAX_WIRE_BYTES,
            wire + b' garbage',
        )
        for raw in variants:
            with self.subTest(raw=raw[:50]), self.assertRaises(PlacementError):
                parse_receipt(raw)
        dictionary["other"] = "unexpected"
        with self.assertRaises(PlacementError):
            parse_receipt(json.dumps(dictionary).encode())
        dictionary.pop("other")
        dictionary.pop("plan_sha256")
        with self.assertRaises(PlacementError):
            parse_receipt(json.dumps(dictionary).encode())

    def test_receipt_strict_schema_hashes_epoch_and_wire_size(self):
        good = dataclasses.asdict(self.seal())
        bad_fields = (
            {"schema": "future/2"},
            {"epoch": True},
            {"epoch": -1},
            {"epoch": 2**63},
            {"inputs_sha256": "A" * 64},
            {"receipt_sha256": "0" * 63},
        )
        for delta in bad_fields:
            with self.subTest(delta=delta), self.assertRaises(PlacementError):
                parse_receipt(json.dumps({**good, **delta}).encode())
        with self.assertRaises(PlacementError):
            parse_receipt(b"")
        with self.assertRaises(PlacementError):
            parse_receipt("not-bytes")

    def test_invalid_safety_plan_never_receives_receipt(self):
        malformed = (
            PlacementPlan((Assignment("a", "server"),
                           Assignment("b", "server")), ()),
            PlacementPlan((Assignment("a", "unknown"),), ("b",)),
            PlacementPlan((Assignment("a", "phone"),), ()),
        )
        for plan in malformed:
            with self.subTest(plan=plan), self.assertRaises(PlacementError):
                self.seal(plan=plan)


if __name__ == "__main__":
    unittest.main()
