import dataclasses
import unittest

from mojum.mesh_attestation import (
    AttestationDenied, AttestationError, MAX_TTL_SECONDS, NodeAttestation,
    SCHEMA, sign_attestation, verify_attestation, verify_offer,
)
from mojum.mesh_contract import Offer, PROTOCOL

KEY = b"test-authority-key-material-32-bytes-minimum!"
KEYS = {"root-2026": KEY}


def attestation(**changes):
    values = dict(
        key_id="root-2026",
        authority_key=KEY,
        node_id="node-01",
        capabilities=("cpu", "memory.read", "world.room"),
        issued_at=1_000,
        expires_at=2_000,
        statement_id="0123456789abcdef0123456789abcdef",
    )
    values.update(changes)
    key = values.pop("authority_key")
    return sign_attestation(authority_key=key, **values)


class MeshAttestationTests(unittest.TestCase):
    def test_sign_and_verify_round_trip(self):
        statement = attestation()
        self.assertEqual(statement.schema, SCHEMA)
        self.assertEqual(statement.protocol, PROTOCOL)
        self.assertEqual(statement.capabilities, ("cpu", "memory.read", "world.room"))
        self.assertIs(verify_attestation(statement, KEYS, now=1_500), statement)

    def test_offer_must_match_identity_protocol_and_exact_capabilities(self):
        statement = attestation()
        good = Offer(PROTOCOL, "node-01", ("world.room", "cpu", "memory.read"))
        self.assertIs(verify_offer(good, statement, KEYS, now=1_500), statement)
        wrong_node = Offer(PROTOCOL, "other-node", good.capabilities)
        with self.assertRaises(AttestationDenied):
            verify_offer(wrong_node, statement, KEYS, now=1_500)
        extra = Offer(PROTOCOL, "node-01", good.capabilities + ("net/tcp",))
        with self.assertRaises(AttestationDenied):
            verify_offer(extra, statement, KEYS, now=1_500)
        fewer = Offer(PROTOCOL, "node-01", ("cpu",))
        with self.assertRaises(AttestationDenied):
            verify_offer(fewer, statement, KEYS, now=1_500)

    def test_tampering_with_any_signed_field_is_rejected(self):
        statement = attestation()
        mutations = (
            {"node_id": "node-02"},
            {"protocol": "future/2"},
            {"capabilities": ("cpu",)},
            {"issued_at": 1_001},
            {"expires_at": 1_999},
            {"statement_id": "f" * 32},
            {"key_id": "other-root"},
        )
        for mutation in mutations:
            changed = dataclasses.replace(statement, **mutation)
            with self.subTest(mutation=mutation), self.assertRaises(
                    (AttestationDenied, AttestationError)):
                verify_attestation(changed, KEYS, now=1_500)

    def test_rejects_hostile_non_string_authority_map_keys_before_lookup(self):
        class HostileKey:
            def __hash__(self):
                return hash("root-2026")

            def __eq__(self, _other):
                raise RuntimeError("untrusted key equality executed")

        statement = attestation()
        hostile = {HostileKey(): KEY}
        with self.assertRaises(AttestationError):
            verify_attestation(statement, hostile, now=1_500)

    def test_rejects_oversized_authority_key_map_before_iteration(self):
        statement = attestation()
        oversized = {"root-2026": KEY}
        oversized.update({f"extra-{index}": KEY for index in range(128)})
        with self.assertRaises(AttestationError):
            verify_attestation(statement, oversized, now=1_500)

    def test_validates_all_authority_map_entries(self):
        statement = attestation()
        with self.assertRaises(AttestationError):
            verify_attestation(statement, {"root-2026": KEY, "invalid key": KEY}, now=1_500)
        with self.assertRaises(AttestationError):
            verify_attestation(statement, {"root-2026": KEY, "other": b"short"}, now=1_500)

    def test_wrong_or_unknown_authority_key_denied(self):
        statement = attestation()
        with self.assertRaises(AttestationDenied):
            verify_attestation(statement, {"root-2026": b"x" * 32}, now=1_500)
        with self.assertRaises(AttestationDenied):
            verify_attestation(statement, {}, now=1_500)

    def test_expiry_and_future_issue_time(self):
        statement = attestation()
        with self.assertRaises(AttestationDenied):
            verify_attestation(statement, KEYS, now=2_000)
        future = attestation(issued_at=10_000, expires_at=11_000)
        with self.assertRaises(AttestationDenied):
            verify_attestation(future, KEYS, now=1_500)
        boundary = attestation(issued_at=1_800, expires_at=2_800)
        self.assertIs(verify_attestation(boundary, KEYS, now=1_500), boundary)

    def test_lifetime_is_bounded(self):
        with self.assertRaises(AttestationError):
            attestation(expires_at=1_000 + MAX_TTL_SECONDS + 1)
        with self.assertRaises(AttestationError):
            attestation(expires_at=1_000)

    def test_strict_primitive_types(self):
        for changes in (
            {"issued_at": True},
            {"expires_at": 2_000.0},
            {"node_id": type("Text", (str,), {})("node-01")},
            {"capabilities": ("cpu", object())},
        ):
            with self.subTest(changes=changes), self.assertRaises(AttestationError):
                attestation(**changes)
        with self.assertRaises(AttestationError):
            verify_attestation(attestation(), KEYS, now=True)
        with self.assertRaises(AttestationError):
            verify_attestation(attestation(), [], now=1_500)

    def test_invalid_capability_and_noncanonical_duplicates_rejected(self):
        for capabilities in (("CPU",), ("../secret",), ("cpu", "cpu")):
            with self.subTest(capabilities=capabilities), self.assertRaises(AttestationError):
                attestation(capabilities=capabilities)

    def test_bad_key_sizes_rejected(self):
        for key in (b"", b"x" * 31, b"x" * 129):
            with self.subTest(size=len(key)), self.assertRaises(AttestationError):
                attestation(authority_key=key)

    def test_attestation_subclasses_and_forged_signature_rejected(self):
        statement = attestation()
        class Child(NodeAttestation):
            pass
        forged = Child(*dataclasses.astuple(statement))
        with self.assertRaises(AttestationError):
            verify_attestation(forged, KEYS, now=1_500)
        changed_sig = dataclasses.replace(statement, signature="0" * 64)
        with self.assertRaises(AttestationDenied):
            verify_attestation(changed_sig, KEYS, now=1_500)

    def test_offer_wrong_type_and_invalid_offer_rejected(self):
        statement = attestation()
        with self.assertRaises(AttestationError):
            verify_offer(object(), statement, KEYS, now=1_500)
        bad = Offer(PROTOCOL, "node-01", ("../secret",))
        with self.assertRaises(AttestationError):
            verify_offer(bad, statement, KEYS, now=1_500)

    def test_authority_signature_is_deterministic_for_same_statement(self):
        one = attestation()
        two = attestation()
        self.assertEqual(one.signature, two.signature)
        self.assertEqual(one, two)


if __name__ == "__main__":
    unittest.main()
