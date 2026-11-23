# Archive signing-key rotation chain R0.1

## Purpose
This opt-in, offline helper verifies a caller-supplied chain of owner-authorized
archive signing-key transitions. It is a detached metadata contract, not a live
key manager, archive reader, transport, or deployment.

## Trust model
- The caller supplies the initial key through a protected channel and separately
  pins the expected terminal SHA-256 fingerprint.
- Every transition is HMAC-SHA256 authorized by the *previous* key and binds the
  schema, contiguous epoch, previous fingerprint, and next fingerprint.
- Intermediate keys are supplied explicitly in a fingerprint-to-key keyring.
  They are used only to verify the next transition; the report never returns key
  material.
- Epoch gaps, replay, rollback/cycles, mismatched keys, malformed records, and a
  terminal fingerprint mismatch fail closed.
- The chain digest is a consistency receipt, not a signature or independent
  timestamp. HMAC is symmetric: anyone holding an old key can authorize a new
  key. Protect and rotate keys out of band.

## Bounds and privacy
Python 3.11+, standard library only; at most 32 transitions; exact native types;
keys are 32..4096 bytes; no filesystem, network, SQLite, Noosphere, subprocess,
or environment access. Tests use synthetic keys only. Never put key bytes or
private archive payloads in issue comments, reports, logs, or model prompts.

## Deliberate non-claims
This does not rotate keys in #153 receipts, update existing archives, encrypt
data, provide asymmetric signatures, prove hardware custody, prevent an
authorized old-key holder from signing a malicious rotation, or solve
multi-device consensus/anti-rollback. Integration requires independent security
review, key custody design, exact-head CI, and an explicit migration protocol.
