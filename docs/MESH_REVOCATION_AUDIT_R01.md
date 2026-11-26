# Mesh peer-revocation audit R0.1

`mojum.mesh.revocation-audit/1` is an offline, read-only consistency audit for explicitly supplied `PUBLIC_SYNTHETIC` peer capability events.

## Example

```python
import json
from mojum.mesh_revocation_audit import audit_peer_revocations

packet = {
    "schema": "mojum.mesh.revocation-audit/1",
    "visibility": "PUBLIC_SYNTHETIC",
    "as_of_sequence": 9,
    "events": [
        {"event_id": "grant-1", "peer_id": "node-b",
         "capability": "memory.read", "kind": "grant", "sequence": 3},
        {"event_id": "revoke-1", "peer_id": "node-b",
         "capability": "memory.read", "kind": "revoke", "sequence": 8},
    ],
    "queries": [{"peer_id": "node-b", "capability": "memory.read"}],
}
report = audit_peer_revocations(json.dumps(packet))
assert report.decisions[0].reason == "revoked"
```

## Rules and limits

- A capability is allowed only when the latest event at or before `as_of_sequence` is `grant`.
- A revoke denies that peer/capability pair. A later, strictly higher-sequence grant can restore it.
- Event sequences must be strictly increasing in input order for each peer/capability pair. Duplicate IDs and duplicate sequence positions are rejected.
- JSON duplicate keys, unexpected keys, bool-as-int values, malformed shapes, unknown event kinds and unbounded inputs fail closed.
- Limits: 1 MiB input, 10,000 events, 2,000 requested pairs, 128-character identifiers and a 64-character capability label.
- Output decisions are immutable and sorted; the SHA-256 digest covers normalized accepted input.

## Security boundary

This is **not** a cryptographic proof, peer authentication, live revocation distribution, a quorum or writer-fencing mechanism. It cannot prove that a disconnected peer has stopped acting or erase data previously disclosed to it. The caller must supply a complete, trustworthy event history; the audit has no network, disk or runtime side effects.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_revocation_audit.py' -v
```
