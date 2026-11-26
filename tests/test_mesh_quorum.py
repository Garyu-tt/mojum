import unittest

from mojum.mesh_quorum import (
    SCHEMA, MeshQuorumError, evaluate_quorum,
)


class MeshQuorumTests(unittest.TestCase):
    MEMBERS = ("a", "b", "c")
    ORGANISM = "dante-01"
    NOW = 10_000_000_000

    def vote(self, voter, candidate="b", *, vote_id=None, epoch=8,
             issued=None, expires=None, organism=None, schema=SCHEMA):
        return {
            "schema": schema,
            "organism_id": self.ORGANISM if organism is None else organism,
            "voter_id": voter,
            "candidate_id": candidate,
            "epoch": epoch,
            "issued_at_ns": self.NOW - 1_000_000_000 if issued is None else issued,
            "expires_at_ns": self.NOW + 1_000_000_000 if expires is None else expires,
            "vote_id": ("%032x" % (ord(voter[0]) + 1)) if vote_id is None else vote_id,
        }

    def evaluate(self, votes, **kwargs):
        args = dict(organism_id=self.ORGANISM, members=self.MEMBERS,
                    current_epoch=7, vote_receipts=tuple(votes), now_ns=self.NOW)
        args.update(kwargs)
        return evaluate_quorum(**args)

    def test_two_of_three_grants_next_epoch(self):
        result = self.evaluate([self.vote("a"), self.vote("c")])
        self.assertTrue(result.granted)
        self.assertEqual((result.candidate_id, result.epoch, result.support), ("b", 8, 2))
        self.assertEqual(result.reason, "majority")

    def test_three_votes_two_to_one_grants_strict_majority(self):
        result = self.evaluate([self.vote("a"), self.vote("b"), self.vote("c", "a")])
        self.assertTrue(result.granted)
        self.assertEqual((result.candidate_id, result.support), ("b", 2))

    def test_two_node_membership_never_auto_fails_over(self):
        with self.assertRaisesRegex(MeshQuorumError, "exactly three"):
            self.evaluate([self.vote("a"), self.vote("b")], members=("a", "b"))

    def test_one_vote_is_insufficient(self):
        result = self.evaluate([self.vote("a")])
        self.assertFalse(result.granted)
        self.assertEqual(result.reason, "insufficient_votes")

    def test_split_vote_has_no_majority(self):
        result = self.evaluate([self.vote("a", "b"), self.vote("c", "a")])
        self.assertFalse(result.granted)
        self.assertEqual((result.candidate_id, result.reason), (None, "no_majority"))

    def test_three_way_split_has_no_majority(self):
        result = self.evaluate([self.vote("a", "a"), self.vote("b", "b"), self.vote("c", "c")])
        self.assertFalse(result.granted)
        self.assertEqual(result.reason, "no_majority")

    def test_deterministic_digest_ignores_vote_input_order(self):
        a, b = self.vote("a"), self.vote("c")
        self.assertEqual(self.evaluate([a, b]).digest, self.evaluate([b, a]).digest)

    def test_duplicate_voter_rejected(self):
        with self.assertRaisesRegex(MeshQuorumError, "duplicate voter"):
            self.evaluate([self.vote("a"), self.vote("a", vote_id="f" * 32)])

    def test_duplicate_or_previously_seen_vote_id_rejected(self):
        vote = self.vote("a", vote_id="1" * 32)
        with self.assertRaisesRegex(MeshQuorumError, "duplicate vote id"):
            self.evaluate([vote, self.vote("b", vote_id="1" * 32)])
        with self.assertRaisesRegex(MeshQuorumError, "replayed"):
            self.evaluate([vote, self.vote("b", vote_id="2" * 32)],
                          seen_vote_ids=("1" * 32,))

    def test_stale_or_skipped_epoch_rejected(self):
        for epoch in (7, 9):
            with self.subTest(epoch=epoch), self.assertRaisesRegex(MeshQuorumError, "epoch"):
                self.evaluate([self.vote("a", epoch=epoch)])

    def test_foreign_voter_and_candidate_rejected(self):
        with self.assertRaisesRegex(MeshQuorumError, "members"):
            self.evaluate([self.vote("foreign")])
        with self.assertRaisesRegex(MeshQuorumError, "members"):
            self.evaluate([self.vote("a", "foreign")])

    def test_expired_future_and_overlong_votes_rejected(self):
        bad = (
            self.vote("a", expires=self.NOW),
            self.vote("a", issued=self.NOW + 1),
            self.vote("a", issued=self.NOW - 400_000_000_000,
                      expires=self.NOW + 1),
        )
        for vote in bad:
            with self.subTest(vote=vote), self.assertRaises(MeshQuorumError):
                self.evaluate([vote])

    def test_wrong_organism_and_schema_rejected(self):
        for vote in (self.vote("a", organism="another"),
                     self.vote("a", schema="mojum.mesh-vote/99")):
            with self.subTest(vote=vote), self.assertRaises(MeshQuorumError):
                self.evaluate([vote])

    def test_exact_types_reject_bool_epoch_and_timestamp(self):
        for changes in ({"epoch": True}, {"issued_at_ns": False},
                        {"expires_at_ns": True}):
            vote = self.vote("a")
            vote.update(changes)
            with self.subTest(changes=changes), self.assertRaises(MeshQuorumError):
                self.evaluate([vote])

    def test_malformed_members_and_membership_order_rejected(self):
        for members in (("a", "b"), ("a", "a", "c"), ("b", "a", "c"),
                        ("a", "b", "../c"), ["a", "b", "c"]):
            with self.subTest(members=members), self.assertRaises(MeshQuorumError):
                self.evaluate([], members=members)

    def test_unknown_fields_and_non_dict_receipts_rejected(self):
        vote = self.vote("a")
        vote["signature"] = "caller-claims-verified"
        with self.assertRaisesRegex(MeshQuorumError, "shape"):
            self.evaluate([vote])
        with self.assertRaises(MeshQuorumError):
            self.evaluate([[]])

    def test_caller_inputs_are_not_mutated(self):
        a, b = self.vote("a"), self.vote("c")
        before = (a.copy(), b.copy())
        self.evaluate([a, b])
        self.assertEqual((a, b), before)

    def test_malformed_vote_ids_and_input_budget_rejected(self):
        for vote_id in ("", "G" * 32, "0" * 31, "0" * 33, 1):
            with self.subTest(vote_id=vote_id), self.assertRaises(MeshQuorumError):
                self.evaluate([self.vote("a", vote_id=vote_id)])
        with self.assertRaisesRegex(MeshQuorumError, "at most three"):
            self.evaluate([self.vote("a"), self.vote("b"),
                           self.vote("c"), self.vote("c")])

    def test_epoch_exhaustion_fails_closed(self):
        with self.assertRaises(MeshQuorumError):
            self.evaluate([], current_epoch=(1 << 63) - 1)

    def test_empty_vote_set_is_a_safe_no_quorum(self):
        result = self.evaluate([])
        self.assertFalse(result.granted)
        self.assertEqual((result.reason, result.support), ("insufficient_votes", 0))


if __name__ == "__main__":
    unittest.main()
