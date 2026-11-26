"""Adversarial offline tests for writer-fencing admission."""
import copy
import unittest
from mojum.writer_fencing import MAX_COUNTER, SCHEMA, FenceDecision, evaluate_writer_fence

def token(*, organism="dante", scope="canonical-state", owner="node-a", epoch=1, revision=1):
    return {"schema": SCHEMA, "organism_id": organism, "scope": scope,
            "owner_id": owner, "epoch": epoch, "revision": revision}

class WriterFenceTests(unittest.TestCase):
    def test_initial_admission_is_deterministic(self):
        candidate = token()
        first = evaluate_writer_fence(candidate)
        self.assertEqual(first, evaluate_writer_fence(copy.deepcopy(candidate)))
        self.assertIsInstance(first, FenceDecision)
        self.assertTrue(first.accepted)
        self.assertEqual(first.reason, "ACCEPTED_INITIAL")
        self.assertEqual(len(first.receipt), 64)
        self.assertEqual(evaluate_writer_fence(token(epoch=2)).reason,
                         "REJECT_MALFORMED")

    def test_same_owner_must_advance_revision(self):
        old = token(epoch=4, revision=8)
        good = evaluate_writer_fence(token(epoch=4, revision=9), old)
        self.assertTrue(good.accepted)
        self.assertEqual(good.reason, "ACCEPTED_ADVANCE")
        self.assertEqual(evaluate_writer_fence(token(epoch=4, revision=8), old).reason,
                         "REJECT_DUPLICATE_REVISION")
        self.assertEqual(evaluate_writer_fence(token(epoch=4, revision=7), old).reason,
                         "REJECT_REVISION_ROLLBACK")

    def test_new_epoch_may_change_owner_but_stale_epoch_may_not(self):
        old = token(owner="node-a", epoch=4, revision=99)
        new = evaluate_writer_fence(token(owner="node-b", epoch=5, revision=1), old)
        self.assertTrue(new.accepted)
        self.assertEqual(new.reason, "ACCEPTED_NEW_EPOCH")
        self.assertEqual(evaluate_writer_fence(
            token(owner="node-b", epoch=3, revision=MAX_COUNTER), old
        ).reason, "REJECT_STALE_EPOCH")

    def test_same_epoch_competing_owner_is_rejected(self):
        result = evaluate_writer_fence(token(owner="node-b", epoch=4, revision=100),
                                       token(owner="node-a", epoch=4, revision=99))
        self.assertEqual(result.reason, "REJECT_OWNER_CONFLICT")

    def test_organism_and_scope_are_bound(self):
        prior = token()
        for candidate in (token(organism="other"), token(scope="metadata")):
            self.assertEqual(evaluate_writer_fence(candidate, prior).reason,
                             "REJECT_SCOPE_MISMATCH")

    def test_exact_schema_and_native_types(self):
        base = token()
        extra = dict(base, extra="x")
        missing = dict(base)
        del missing["revision"]
        malformed = [None, [], "token", extra, missing, dict(base, schema="future/2"),
                     dict(base, epoch=True), dict(base, revision=False),
                     dict(base, epoch=1.0), dict(base, revision="2"),
                     dict(base, organism_id=object()), dict(base, scope=""),
                     dict(base, owner_id=" node-a"), dict(base, owner_id="x\\ny")]
        for candidate in malformed:
            with self.subTest(candidate=repr(candidate)[:80]):
                self.assertEqual(evaluate_writer_fence(candidate).reason,
                                 "REJECT_MALFORMED")

    def test_oversized_native_keys_are_rejected_before_set_comparison(self):
        from unittest.mock import patch

        candidate = token()
        candidate["x" * 1_000_000] = "ignored"
        with patch(
            "mojum.writer_fencing.frozenset",
            side_effect=AssertionError("oversized key must be rejected before hashing"),
            create=True,
        ) as key_set:
            result = evaluate_writer_fence(candidate)
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "REJECT_MALFORMED")
            key_set.assert_not_called()

    def test_malformed_high_water_fails_closed(self):
        result = evaluate_writer_fence(token(), {"not": "a high-water mark"})
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "REJECT_MALFORMED")

    def test_counter_bounds_and_boolean_rejection(self):
        for field in ("epoch", "revision"):
            for value in (0, -1, MAX_COUNTER + 1, True, 1.0):
                with self.subTest(field=field, value=repr(value)):
                    self.assertEqual(
                        evaluate_writer_fence(token(**{field: value})).reason,
                        "REJECT_MALFORMED",
                    )
        # The first token is deliberately fixed at epoch=1/revision=1.
        # Exercise the numeric ceiling through valid advancing transitions.
        previous_epoch = token(epoch=MAX_COUNTER - 1, revision=MAX_COUNTER)
        new_epoch = evaluate_writer_fence(
            token(epoch=MAX_COUNTER, revision=MAX_COUNTER), previous_epoch
        )
        self.assertTrue(new_epoch.accepted)
        self.assertEqual(new_epoch.reason, "ACCEPTED_NEW_EPOCH")

        previous_revision = token(epoch=MAX_COUNTER, revision=MAX_COUNTER - 1)
        max_revision = evaluate_writer_fence(
            token(epoch=MAX_COUNTER, revision=MAX_COUNTER), previous_revision
        )
        self.assertTrue(max_revision.accepted)
        self.assertEqual(max_revision.reason, "ACCEPTED_ADVANCE")

    def test_inputs_are_not_mutated(self):
        candidate, high = token(epoch=2), token(epoch=1, revision=7)
        before_candidate, before_high = copy.deepcopy(candidate), copy.deepcopy(high)
        evaluate_writer_fence(candidate, high)
        self.assertEqual(candidate, before_candidate)
        self.assertEqual(high, before_high)

    def test_receipt_commits_both_sides_and_decision(self):
        prior = token(epoch=2, revision=3)
        accepted = evaluate_writer_fence(token(epoch=2, revision=4), prior)
        rejected = evaluate_writer_fence(token(epoch=2, revision=3), prior)
        self.assertNotEqual(accepted.receipt, rejected.receipt)
        self.assertNotEqual(accepted.candidate_digest, rejected.candidate_digest)
        self.assertEqual(accepted.high_water_digest, rejected.high_water_digest)

    def test_reason_vocabulary_is_bounded(self):
        self.assertEqual(evaluate_writer_fence({}).reason, "REJECT_MALFORMED")
        self.assertEqual(evaluate_writer_fence(token()).reason, "ACCEPTED_INITIAL")

if __name__ == "__main__":
    unittest.main()
