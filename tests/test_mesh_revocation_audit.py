import json
import unittest

from mojum.mesh_revocation_audit import (
    MAX_EVENTS,
    RevocationAuditError,
    audit_peer_revocations,
)


def document(*, events=None, queries=None, as_of=10, visibility="PUBLIC_SYNTHETIC"):
    return json.dumps({
        "schema": "mojum.mesh.revocation-audit/1",
        "visibility": visibility,
        "as_of_sequence": as_of,
        "events": [] if events is None else events,
        "queries": [{"peer_id": "node-a", "capability": "memory.read"}] if queries is None else queries,
    })


def event(event_id, kind, sequence, peer="node-a", capability="memory.read"):
    return {"event_id": event_id, "peer_id": peer, "capability": capability,
            "kind": kind, "sequence": sequence}


class MeshRevocationAuditTests(unittest.TestCase):
    def test_active_grant(self):
        result = audit_peer_revocations(document(events=[event("e1", "grant", 2)]))
        decision = result.decisions[0]
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "active_grant")
        self.assertEqual(decision.event_sequence, 2)

    def test_revocation_supersedes_grant(self):
        result = audit_peer_revocations(document(events=[
            event("e1", "grant", 2), event("e2", "revoke", 3)
        ]))
        self.assertFalse(result.decisions[0].allowed)
        self.assertEqual(result.decisions[0].reason, "revoked")

    def test_later_explicit_grant_restores_capability(self):
        result = audit_peer_revocations(document(events=[
            event("e1", "grant", 2), event("e2", "revoke", 3),
            event("e3", "grant", 4),
        ]))
        self.assertTrue(result.decisions[0].allowed)
        self.assertEqual(result.decisions[0].event_sequence, 4)

    def test_future_events_do_not_affect_as_of_decision(self):
        result = audit_peer_revocations(document(events=[
            event("e1", "grant", 2), event("e2", "revoke", 11)
        ], as_of=10))
        self.assertTrue(result.decisions[0].allowed)

    def test_missing_grant_is_denied(self):
        result = audit_peer_revocations(document())
        self.assertFalse(result.decisions[0].allowed)
        self.assertEqual(result.decisions[0].reason, "no_grant")

    def test_capabilities_are_independent(self):
        result = audit_peer_revocations(document(events=[
            event("e1", "grant", 1, capability="memory.read"),
            event("e2", "revoke", 2, capability="jobs.execute"),
        ], queries=[
            {"peer_id": "node-a", "capability": "memory.read"},
            {"peer_id": "node-a", "capability": "jobs.execute"},
        ]))
        self.assertEqual([item.allowed for item in result.decisions], [False, True]
                         if result.decisions[0].capability == "jobs.execute" else [True, False])

    def test_ordered_decisions_and_digest_are_deterministic(self):
        events = [event("e2", "revoke", 3, peer="node-b"),
                  event("e1", "grant", 2, peer="node-a")]
        queries = [{"peer_id": "node-b", "capability": "memory.read"},
                   {"peer_id": "node-a", "capability": "memory.read"}]
        a = audit_peer_revocations(document(events=events, queries=queries))
        b = audit_peer_revocations(document(events=events, queries=list(reversed(queries))))
        self.assertEqual(a.decisions, b.decisions)
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(len(a.digest), 64)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations('{"schema":"a","schema":"b"}')

    def test_wrong_visibility_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(visibility="PRIVATE"))

    def test_unknown_and_missing_fields_rejected(self):
        item = event("e1", "grant", 1)
        item["extra"] = True
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(events=[item]))

    def test_duplicate_event_ids_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(events=[
                event("same", "grant", 1), event("same", "revoke", 2)
            ]))

    def test_duplicate_peer_sequence_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(events=[
                event("e1", "grant", 1), event("e2", "revoke", 1)
            ]))

    def test_nonmonotonic_input_order_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(events=[
                event("e1", "grant", 3), event("e2", "revoke", 2)
            ]))

    def test_bool_is_not_sequence(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(as_of=True))

    def test_lone_surrogate_identifiers_rejected_with_public_error(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(queries=[
                {"peer_id": "\ud800", "capability": "memory.read"}
            ]))

    def test_unbounded_values_rejected(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(queries=[
                {"peer_id": "x" * 129, "capability": "memory.read"}
            ]))
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(document(queries=[]))

    def test_event_budget_is_enforced(self):
        # The limit is checked before traversing the individual records.
        raw = json.dumps({
            "schema": "mojum.mesh.revocation-audit/1",
            "visibility": "PUBLIC_SYNTHETIC",
            "as_of_sequence": 1,
            "events": [None] * (MAX_EVENTS + 1),
            "queries": [{"peer_id": "node-a", "capability": "memory.read"}],
        })
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(raw)

    def test_oversized_text_is_rejected_by_character_preflight(self):
        with self.assertRaisesRegex(RevocationAuditError, "byte budget"):
            audit_peer_revocations("x" * (1_048_576 + 1))

    def test_input_must_be_native_string(self):
        with self.assertRaises(RevocationAuditError):
            audit_peer_revocations(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
