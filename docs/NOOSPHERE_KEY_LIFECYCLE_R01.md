# Noosphere key lifecycle metadata R0.1

This opt-in helper validates caller-supplied metadata for Noosphere export keys.
It does not handle key bytes, generate secrets, encrypt/decrypt payloads, persist
metadata, or establish identity, authorization, or cryptographic authenticity.

## Contract

- Schema: `mojum.noosphere-key-lifecycle/1`.
- One purpose and algorithm are admitted: `noosphere-export` and
  `AES-256-GCM`; this prevents metadata from silently crossing domains.
- Key IDs are bounded ASCII identifiers; a keyring contains 1..128 unique keys.
- Timestamps are non-negative integer logical/Unix timestamps (booleans rejected).
  Activation follows creation; retirement/revocation cannot precede activation
  or lie in the future relative to the supplied audit time.
- Retired keys cannot encrypt new exports but may decrypt historical exports
  while material remains available. Revoked or unavailable keys are denied
  for both operations.
- The canonical digest binds normalized metadata only. It is not a signature
  and cannot prove that caller-supplied metadata is true.

## Example

```python
from mojum.noosphere_key_lifecycle import validate_keyring, admit_key_operation

report = validate_keyring([record], now=1_800_000_000)
decision = admit_key_operation(record, operation="encrypt", at=1_800_000_000)
```

Keep key material in an external owner-controlled secret store. Rotation,
secure deletion, backup, access control, and actual cryptographic operations
remain responsibilities of the caller. No Noosphere journal or runtime
integration is performed by this module.
