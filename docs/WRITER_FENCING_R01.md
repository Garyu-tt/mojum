# Writer fencing admission R0.1

An opt-in, offline, standard-library-only decision helper for Continuity Mesh (#12). It evaluates candidate writer fencing metadata against a caller-supplied high-water token.

## Contract

Wire schema: `mojum.mesh.writer-fence/1`.

Each candidate contains exactly schema, organism_id, scope, owner_id, epoch, revision. Identifiers are bounded canonical ASCII strings. Epoch and revision are exact native positive integers no larger than 2^63-1; booleans and numeric lookalikes are rejected.

The evaluator rejects malformed or cross-scope tokens, stale epochs, same-epoch owner conflicts, revision rollback and duplicate revisions. A new owner is accepted only with a strictly larger epoch. Within one epoch the owner must stay the same and revision must increase.

The decision contains candidate/high-water SHA-256 digests and a deterministic receipt over the decision. Digests provide consistency evidence only, not signatures, authentication or tamper resistance against an attacker able to replace both records.

## Example

~~~python
from mojum.writer_fencing import SCHEMA, evaluate_writer_fence

previous = {
    "schema": SCHEMA, "organism_id": "dante",
    "scope": "canonical-state", "owner_id": "node-a",
    "epoch": 3, "revision": 14,
}
candidate = {**previous, "revision": 15}
decision = evaluate_writer_fence(candidate, previous)
assert decision.accepted
assert decision.reason == "ACCEPTED_ADVANCE"
~~~

## Critical limitations

This is **not** a distributed lease implementation. It does not create tokens, verify signatures, authenticate node identities, store the high-water mark, consult quorum, prove lease freshness, fence a process, or perform writes. The caller must load the prior accepted token from a trusted durable consensus-backed source and atomically persist any accepted advancement before allowing a write. Two callers using stale copies of the same high-water mark can both receive a positive advisory answer; this module alone cannot prevent split brain.

No database, network, PRIVATE Noosphere, runtime wiring, host/device control, or mandatory dependencies are used. Intended for deterministic CPU-only fixtures and independent review.
