# Mesh node attestation R0.1

## Example

    from mojum.mesh_attestation import sign_attestation, verify_offer
    from mojum.mesh_contract import Offer, PROTOCOL

    authority_key = b"replace-with-at-least-32-random-secret-bytes"
    statement = sign_attestation(
        key_id="mesh-root-01",
        authority_key=authority_key,
        node_id="peer-02",
        capabilities=("cpu", "memory.read"),
        issued_at=1_800_000_000,
        expires_at=1_800_003_600,
        statement_id="0123456789abcdef0123456789abcdef",
    )
    verify_offer(
        Offer(PROTOCOL, "peer-02", ("memory.read", "cpu")),
        statement,
        {"mesh-root-01": authority_key},
        now=1_800_000_100,
    )

## Contract

This additive CPU/stdlib module verifies an authority-issued, versioned node
statement and binds a presented mesh capability offer to the exact node ID,
protocol and capability set covered by the statement. It enforces canonical
capability ordering, a maximum 30-day lifetime, a 5-minute future clock
allowance, bounded key sizes and constant-time HMAC comparison.

Run the focused suite:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_attestation.py' -v

## Security boundary and limitations

- HMAC-SHA256 is a **shared-secret MAC**, not an asymmetric digital signature.
  Every verifier holding an authority key can also mint statements. Use a
  separate signing service and tightly control distribution of verification
  secrets; this module itself does not create, persist, rotate or protect keys.
- The verifier consumes an explicit caller-supplied key map. That map is the
  trust root. Before lookup it rejects more than 128 entries, noncanonical
  key IDs and any value that is not an exact 32..128-byte key. This prevents
  custom Python key objects from running equality hooks during a dictionary
  lookup. No automatic trust, network enrollment, node discovery, TLS,
  hardware-backed identity, replay cache, durable registry or revocation
  propagation is implemented.
- statement_id is signed and uniquely intended by the issuer, but uniqueness
  is not enforced without a durable registry. A revoked key must be removed
  from each verifier's trusted key map through an independently managed policy.
- Expiry depends on the caller's supplied Unix time. The module does not read
  wall-clock time and is deterministic for offline tests.
- The offer must match the signed capability set exactly. This is an identity
  and offer-binding barrier, not proof that the remote endpoint controls that
  node ID, is live, or will execute a task safely.
- No mesh_contract.py, placement, Noosphere, private journal, runtime,
  transport, live host, secret store or physical device is modified.

This is a reference trust-boundary slice for issue #12, not completion of
Continuity Mesh enrollment, replication, quorum, failover or revocation.
