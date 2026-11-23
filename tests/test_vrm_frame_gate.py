import hashlib
import unittest

from mojum.vrm_frame_gate import (
    FrameGateError,
    PoseFrameGate,
    PUBLIC_SYNTHETIC,
    SCHEMA,
)


REVISION = hashlib.sha256(b"fixture-body").hexdigest()


def packet(*, sequence=1, tick=1, timestamp_ns=10, revision=REVISION, joints=None):
    return {
        "schema": SCHEMA,
        "epoch": "demo-epoch-1",
        "sequence": sequence,
        "tick": tick,
        "timestamp_ns": timestamp_ns,
        "body_revision": revision,
        "joints": {"root": (0.0, 0.0, 0.0)} if joints is None else joints,
    }


def gate():
    result = PoseFrameGate()
    result.begin_epoch(epoch="demo-epoch-1", body_revision=REVISION)
    return result


class PoseFrameGateTests(unittest.TestCase):
    def test_accepts_and_detaches_valid_frame(self):
        source = packet(joints={"hand": [1, 2, 3], "root": [0, 0, 0]})
        receipt = gate().admit(source, consent=True, source_tag=PUBLIC_SYNTHETIC)
        self.assertEqual(receipt.joints[0][0], "hand")
        self.assertEqual(receipt.joints[0][1], (1.0, 2.0, 3.0))
        self.assertEqual(len(receipt.digest), 64)
        source["joints"]["hand"][0] = 99
        self.assertEqual(receipt.joints[0][1][0], 1.0)

    def test_digest_is_independent_of_joint_input_order(self):
        first = gate().admit(
            packet(joints={"root": [0, 0, 0], "hand": [1, 2, 3]}),
            consent=True, source_tag=PUBLIC_SYNTHETIC,
        )
        second = gate().admit(
            packet(joints={"hand": [1, 2, 3], "root": [0, 0, 0]}),
            consent=True, source_tag=PUBLIC_SYNTHETIC,
        )
        self.assertEqual(first.digest, second.digest)

    def test_requires_explicit_consent_and_source(self):
        with self.assertRaises(FrameGateError):
            gate().admit(packet(), consent=False, source_tag=PUBLIC_SYNTHETIC)
        with self.assertRaises(FrameGateError):
            gate().admit(packet(), consent=True, source_tag="PRIVATE")

    def test_requires_epoch_initialization(self):
        with self.assertRaises(FrameGateError):
            PoseFrameGate().admit(packet(), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_duplicate_or_decreasing_sequence_tick_and_time(self):
        for field, values in (
            ("sequence", (1, 1)),
            ("tick", (1, 1)),
            ("timestamp_ns", (10, 9)),
        ):
            subject = gate()
            subject.admit(packet(), consent=True, source_tag=PUBLIC_SYNTHETIC)
            changed = {"sequence": 2, "tick": 2, "timestamp_ns": 20}
            changed[field] = values[1]
            with self.subTest(field=field), self.assertRaises(FrameGateError):
                subject.admit(packet(**changed), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_changed_body_revision_until_new_epoch(self):
        subject = gate()
        changed_revision = hashlib.sha256(b"new-body").hexdigest()
        with self.assertRaises(FrameGateError):
            subject.admit(packet(revision=changed_revision), consent=True, source_tag=PUBLIC_SYNTHETIC)
        subject.begin_epoch(epoch="demo-epoch-2", body_revision=changed_revision)
        updated = packet(revision=changed_revision)
        updated["epoch"] = "demo-epoch-2"
        receipt = subject.admit(updated, consent=True, source_tag=PUBLIC_SYNTHETIC)
        self.assertEqual(receipt.epoch, "demo-epoch-2")

    def test_rejects_reusing_epoch_on_reset(self):
        subject = gate()
        with self.assertRaises(FrameGateError):
            subject.begin_epoch(epoch="demo-epoch-1", body_revision=REVISION)

    def test_rejects_wrong_epoch_and_revision_shapes(self):
        with self.assertRaises(FrameGateError):
            gate().admit(packet(revision="not-a-digest"), consent=True, source_tag=PUBLIC_SYNTHETIC)
        wrong_epoch = packet()
        wrong_epoch["epoch"] = "other"
        with self.assertRaises(FrameGateError):
            gate().admit(wrong_epoch, consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_unknown_or_missing_packet_fields(self):
        for mutate in (
            lambda value: value.update(extra=True),
            lambda value: value.pop("tick"),
        ):
            candidate = packet()
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(FrameGateError):
                gate().admit(candidate, consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_bool_and_out_of_range_counters(self):
        for field, bad in (("tick", True), ("sequence", -1), ("timestamp_ns", 1 << 63)):
            candidate = packet()
            candidate[field] = bad
            with self.subTest(field=field), self.assertRaises(FrameGateError):
                gate().admit(candidate, consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_invalid_joint_inventory(self):
        for joints in ({}, {"bad/name": [0, 0, 0]}, {"root": [0, 0]}, {"root": [0, 0, 0], "ROOT": [0, 0, 0]}):
            with self.subTest(joints=joints), self.assertRaises(FrameGateError):
                gate().admit(packet(joints=joints), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_non_finite_and_out_of_budget_coordinates(self):
        for coordinate in (float("nan"), float("inf"), 10001.0):
            with self.subTest(coordinate=coordinate), self.assertRaises(FrameGateError):
                gate().admit(packet(joints={"root": [coordinate, 0, 0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejects_bool_coordinate(self):
        with self.assertRaises(FrameGateError):
            gate().admit(packet(joints={"root": [True, 0, 0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_rejected_frame_does_not_advance_gate(self):
        subject = gate()
        with self.assertRaises(FrameGateError):
            subject.admit(packet(sequence=1, tick=1, timestamp_ns=10, joints={"root": [float("nan"), 0, 0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)
        accepted = subject.admit(packet(), consent=True, source_tag=PUBLIC_SYNTHETIC)
        self.assertEqual(accepted.sequence, 1)

    def test_joint_count_limit_is_enforced(self):
        subject = PoseFrameGate(max_joints=1)
        subject.begin_epoch(epoch="demo-epoch-1", body_revision=REVISION)
        with self.assertRaises(FrameGateError):
            subject.admit(packet(joints={"root": [0, 0, 0], "hand": [0, 0, 0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)

    def test_zero_is_canonicalized(self):
        positive = gate().admit(packet(joints={"root": [0.0, 0.0, 0.0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)
        negative = gate().admit(packet(joints={"root": [-0.0, 0.0, -0.0]}), consent=True, source_tag=PUBLIC_SYNTHETIC)
        self.assertEqual(positive.digest, negative.digest)


    def test_rejects_integer_coordinate_that_overflows_float(self):
        with self.assertRaises(FrameGateError):
            gate().admit(
                packet(joints={"root": [10 ** 10000, 0, 0]}),
                consent=True, source_tag=PUBLIC_SYNTHETIC,
            )

    def test_rejects_hostile_schema_value_without_comparing_it(self):
        class Hostile:
            def __eq__(self, other):
                raise AssertionError("hostile equality must not run")

        candidate = packet()
        candidate["schema"] = Hostile()
        with self.assertRaises(FrameGateError):
            gate().admit(candidate, consent=True, source_tag=PUBLIC_SYNTHETIC)


if __name__ == "__main__":
    unittest.main()
