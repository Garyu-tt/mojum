# Mesh revocation snapshot R0.1

Offline stdlib-only policy helper for Continuity Mesh issue #12. It checks
whether an enrolled peer's exact node ID + enrollment ID appears in a fresh,
authority-signed revocation snapshot.

## API

- sign_snapshot(...) creates a deterministic HMAC-SHA256 envelope for an
  already-authorized issuer or synthetic fixture.
- verify_snapshot(..., authority_keys, now_tick, minimum_generation) validates
  schema, bounded key inventory, signature, validity window and caller-supplied
  rollback floor, then returns an in-process verified snapshot object.
- check_peer_access(..., operation="read"|"write") returns the revocation-list
  decision for one attempted protected read or write. It accepts only the exact
  object instance returned by verify_snapshot(); reconstructed or copied
  dataclass instances do not inherit verification provenance.

Authority keys are supplied out of band and must be 32..128 bytes. Snapshot
generations are monotonic only relative to the caller-supplied minimum_generation;
this module does not persist that high-water mark. Snapshots are bounded to
4096 entries and 1 MiB. Revocation entries are unique by enrollment and node
identity, sorted by enrollment ID, and effective no later than snapshot issue.

## Security and operational boundaries

A denied or identity-mismatched peer is not cleared for reads or writes.
A positive allowed_by_revocation_check means only that the pair is absent from
this verified snapshot. It does not authenticate the peer, prove current
enrollment, grant capabilities, or replace authorization at a storage boundary.

This helper does not distribute revocations, authenticate transport, store keys,
persist generation floors, terminate remote sessions, erase data already
disclosed, change SQLite/Noosphere data, elect leaders, or fence a writer.
A stale snapshot must be rejected using a trusted durable minimum_generation;
without external high-water state, replay resistance is limited to the
snapshot validity window.

## Tests

Run:
    PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_revocation.py' -v

Synthetic fixtures only. No network, filesystem, PRIVATE records, hardware,
or production host interaction.
