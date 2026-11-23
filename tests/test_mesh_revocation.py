"""Adversarial tests for the offline mesh revocation policy oracle."""
from dataclasses import replace
import unittest

from mojum.mesh_revocation import (
    MeshRevocationError, VerifiedRevocationSnapshot, check_peer_access,
    sign_snapshot, verify_snapshot,
)

KEY = b"K" * 32
KEYS = {"authority-key-1": KEY}


def envelope(*, revocations=None, generation=4, issued=100, expires=200):
    return sign_snapshot(
        authority_id="mesh-root", key_id="authority-key-1", generation=generation,
        issued_at_tick=issued, expires_at_tick=expires,
        revocations=[] if revocations is None else revocations, authority_key=KEY,
    )


def revoked():
    return {"enrollment_id": "enroll-17", "node_id": "node-17",
            "sequence": 1, "revoked_at_tick": 90}


class HostileKey(str):
    def __hash__(self):
        return hash("schema")

    def __eq__(self, other):
        raise RuntimeError("must not execute hostile equality")


class MeshRevocationTests(unittest.TestCase):
    def test_valid_empty_snapshot_allows_only_revocation_check(self):
        verified = verify_snapshot(envelope(), authority_keys=KEYS, now_tick=150)
        result = check_peer_access(verified, node_id="node-a", enrollment_id="enroll-a",
                                   operation="read", now_tick=150)
        self.assertTrue(result.allowed_by_revocation_check)
        self.assertEqual(result.reason, "not_listed_as_revoked")

    def test_revoked_peer_denied_for_read_and_write(self):
        verified = verify_snapshot(envelope(revocations=[revoked()]),
                                   authority_keys=KEYS, now_tick=150)
        for operation in ("read", "write"):
            with self.subTest(operation=operation):
                result = check_peer_access(verified, node_id="node-17",
                                           enrollment_id="enroll-17",
                                           operation=operation, now_tick=150)
                self.assertFalse(result.allowed_by_revocation_check)
                self.assertEqual(result.reason, "peer_revoked")

    def test_enrollment_or_node_alias_mismatch_fails_closed(self):
        verified = verify_snapshot(envelope(revocations=[revoked()]),
                                   authority_keys=KEYS, now_tick=150)
        for node, enrollment in (("node-17", "other-enroll"), ("other-node", "enroll-17")):
            with self.subTest(node=node, enrollment=enrollment):
                result = check_peer_access(verified, node_id=node, enrollment_id=enrollment,
                                           operation="write", now_tick=150)
                self.assertFalse(result.allowed_by_revocation_check)
                self.assertEqual(result.reason, "revocation_identity_mismatch")

    def test_non_revoked_pair_is_not_matched_by_prefix(self):
        verified = verify_snapshot(envelope(revocations=[revoked()]),
                                   authority_keys=KEYS, now_tick=150)
        result = check_peer_access(verified, node_id="node-170", enrollment_id="enroll-170",
                                   operation="write", now_tick=150)
        self.assertTrue(result.allowed_by_revocation_check)

    def test_signature_tampering_rejected(self):
        value = envelope()
        value["generation"] = 5
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(value, authority_keys=KEYS, now_tick=150)

    def test_unknown_and_wrong_authority_keys_rejected(self):
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys={}, now_tick=150)
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys={"authority-key-1": b"X" * 32},
                            now_tick=150)

    def test_expired_and_future_snapshots_rejected(self):
        value = envelope(issued=100, expires=120)
        for now in (99, 121):
            with self.subTest(now=now), self.assertRaises(MeshRevocationError):
                verify_snapshot(value, authority_keys=KEYS, now_tick=now)

    def test_generation_below_durable_floor_rejected(self):
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys=KEYS, now_tick=150,
                            minimum_generation=5)

    def test_invalid_validity_windows_rejected(self):
        for issued, expires in ((100, 100), (100, 1_000_101), (True, 200)):
            with self.subTest(issued=issued, expires=expires), self.assertRaises(MeshRevocationError):
                envelope(issued=issued, expires=expires)

    def test_duplicate_identity_and_unsorted_entries_rejected(self):
        first = revoked()
        duplicate_node = {**first, "enrollment_id": "enroll-18"}
        unsorted = [{**first, "enrollment_id": "z-last"},
                    {**first, "node_id": "node-18", "enrollment_id": "a-first"}]
        for entries in ([first, duplicate_node], unsorted):
            with self.subTest(entries=entries), self.assertRaises(MeshRevocationError):
                envelope(revocations=entries)

    def test_revocation_cannot_postdate_signed_snapshot(self):
        with self.assertRaises(MeshRevocationError):
            envelope(revocations=[{**revoked(), "revoked_at_tick": 101}], issued=100)

    def test_wrong_native_types_and_bool_as_integer_rejected(self):
        base = dict(authority_id="mesh-root", key_id="authority-key-1", generation=4,
                    issued_at_tick=100, expires_at_tick=200, revocations=[], authority_key=KEY)
        cases = ({"generation": True}, {"generation": 0}, {"expires_at_tick": True},
                 {"authority_id": "bad id"}, {"revocations": {}})
        for change in cases:
            args = {**base, **change}
            with self.subTest(change=change), self.assertRaises(MeshRevocationError):
                sign_snapshot(**args)

    def test_hostile_envelope_and_key_map_keys_rejected(self):
        hostile = {HostileKey("schema"): "forged"}
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(hostile, authority_keys=KEYS, now_tick=150)
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys=hostile, now_tick=150)

    def test_oversized_snapshot_and_revocation_mappings_fail_before_key_scan(self):
        oversized_envelope = envelope()
        oversized_envelope.update({f"extra-{index}": index for index in range(25_000)})
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(oversized_envelope, authority_keys=KEYS, now_tick=150)

        oversized_entry = revoked()
        oversized_entry.update({f"extra-{index}": index for index in range(25_000)})
        with self.assertRaises(MeshRevocationError):
            envelope(revocations=[oversized_entry])

    def test_unbounded_and_invalid_key_maps_rejected(self):
        many = {f"key-{i}": KEY for i in range(129)}
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys=many, now_tick=150)
        with self.assertRaises(MeshRevocationError):
            verify_snapshot(envelope(), authority_keys={"authority-key-1": bytearray(KEY)},
                            now_tick=150)

    def test_verified_snapshot_cannot_be_reused_after_expiry(self):
        verified = verify_snapshot(envelope(issued=100, expires=120),
                                    authority_keys=KEYS, now_tick=110)
        with self.assertRaises(MeshRevocationError):
            check_peer_access(verified, node_id="node-a", enrollment_id="enroll-a",
                              operation="read", now_tick=121)

    def test_unverified_snapshot_instance_cannot_bypass_signature_verification(self):
        forged = VerifiedRevocationSnapshot(
            authority_id="mesh-root", key_id="authority-key-1", generation=99,
            issued_at_tick=100, expires_at_tick=200, entries=(), digest="0" * 64,
        )
        with self.assertRaises(MeshRevocationError):
            check_peer_access(forged, node_id="node-a", enrollment_id="enroll-a",
                              operation="read", now_tick=150)

    def test_verified_snapshot_and_entries_have_no_mutable_instance_dict(self):
        verified = verify_snapshot(
            envelope(revocations=[revoked()]), authority_keys=KEYS, now_tick=150
        )
        with self.assertRaises(AttributeError):
            _ = verified.__dict__
        with self.assertRaises(AttributeError):
            _ = verified.entries[0].__dict__
        with self.assertRaises(AttributeError):
            _ = check_peer_access(
                verified, node_id="node-17", enrollment_id="enroll-17",
                operation="read", now_tick=150
            ).__dict__

    def test_object_setattr_cannot_change_verified_revocation_state(self):
        verified = verify_snapshot(
            envelope(revocations=[revoked()]), authority_keys=KEYS, now_tick=150
        )
        # frozen=True is bypassable with object.__setattr__; the public object
        # must be rejected rather than allowed to erase the verified revocation.
        object.__setattr__(verified, "entries", ())
        with self.assertRaisesRegex(MeshRevocationError, "modified"):
            check_peer_access(verified, node_id="node-17", enrollment_id="enroll-17",
                              operation="write", now_tick=150)

        verified = verify_snapshot(
            envelope(revocations=[revoked()]), authority_keys=KEYS, now_tick=150
        )
        object.__setattr__(verified.entries[0], "node_id", "node-forged")
        with self.assertRaisesRegex(MeshRevocationError, "modified"):
            check_peer_access(verified, node_id="node-17", enrollment_id="enroll-17",
                              operation="write", now_tick=150)

    def test_copied_or_replaced_snapshot_is_not_verification_provenance(self):
        verified = verify_snapshot(envelope(revocations=[revoked()]),
                                   authority_keys=KEYS, now_tick=150)
        forged = replace(verified, entries=())
        with self.assertRaises(MeshRevocationError):
            check_peer_access(forged, node_id="node-17", enrollment_id="enroll-17",
                              operation="read", now_tick=150)

    def test_invalid_operation_rejected(self):
        verified = verify_snapshot(envelope(), authority_keys=KEYS, now_tick=150)
        with self.assertRaises(MeshRevocationError):
            check_peer_access(verified, node_id="node-a", enrollment_id="enroll-a",
                              operation="delete", now_tick=150)

    def test_detached_decision_keeps_digest_and_identity(self):
        signed = envelope(revocations=[revoked()])
        verified = verify_snapshot(signed, authority_keys=KEYS, now_tick=150)
        signed["revocations"][0]["node_id"] = "node-mutated"
        result = check_peer_access(verified, node_id="node-17", enrollment_id="enroll-17",
                                   operation="read", now_tick=150)
        self.assertFalse(result.allowed_by_revocation_check)
        self.assertEqual(result.generation, 4)
        self.assertEqual(len(result.snapshot_digest), 64)


if __name__ == "__main__":
    unittest.main()
