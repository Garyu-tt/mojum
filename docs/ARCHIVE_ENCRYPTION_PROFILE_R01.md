# Archive encryption profile R0.1

This is a **declaration-admission contract**, not an encryption implementation.

## Purpose

Validate a caller-supplied metadata profile before a separately reviewed archive
adapter is allowed to consider encrypting a physical segment. The contract is
stdlib-only, offline, opt-in and limited to records explicitly classified
`PRIVATE`.

## Accepted profile

Schema: `mojum.archive-encryption-profile/1`.

- Cipher allowlist: `AES-256-GCM` or `ChaCha20-Poly1305`.
- Nonce: exactly 12 bytes; authentication tag: exactly 16 bytes.
- Key reference: bounded opaque identifier only. No key bytes, filesystem paths,
  URLs, environment-variable names or secret values are admitted.
- Key version: positive bounded native integer.
- Segment identifier and plaintext/ciphertext SHA-256 digests are required.
- Associated-data declaration is pinned to `mojum.archive-aad/1`.
- Exact key set and exact native Python primitive types are required.
- `private_opt_in=True` is mandatory. The profile itself must say `PRIVATE`
  and `enabled=True`.

The returned frozen value is detached from caller-owned dictionaries. Its SHA-256
digest is a deterministic consistency fingerprint only.

## Example

```python
from mojum.archive_encryption_profile import admit_profile

profile = admit_profile({
    "schema": "mojum.archive-encryption-profile/1",
    "classification": "PRIVATE",
    "enabled": True,
    "cipher": "AES-256-GCM",
    "key_ref": "owner-archive-key",
    "key_version": 1,
    "nonce_bytes": 12,
    "tag_bytes": 16,
    "segment_id": "segment-0001",
    "plaintext_sha256": "a" * 64,
    "ciphertext_sha256": "b" * 64,
    "aad_schema": "mojum.archive-aad/1",
}, private_opt_in=True)
```

## Security boundary and non-claims

- No encryption/decryption, key lookup, nonce generation, file access, archive
  transfer, SQLite access, Noosphere access, network or runtime wiring occurs.
- Caller-provided hashes and the `PRIVATE` label are assertions, not proof of
  plaintext/ciphertext correspondence, source provenance or access permission.
- A deterministic digest is not a MAC, signature, trusted root or anti-rollback
  mechanism.
- This module does not prove nonce uniqueness across segments or invocations.
  A real adapter must generate nonces securely and enforce uniqueness under the
  selected key, bind authenticated associated data to independently verified
  segment identity/sequence, and verify AEAD tags before exposing plaintext.
- Key custody, rotation, revocation, hardware-backed storage, cryptographic
  implementation review, encryption-at-rest, transport encryption, backup
  recovery and platform-specific permissions remain out of scope.
- The allowlist is a policy boundary, not a claim that the local crypto provider
  is installed, FIPS validated, side-channel safe or correctly configured.

No archive bytes or real PRIVATE data are read or modified.
