# QA supervisor audit-chain verifier R0.1

This additive module verifies caller-supplied metadata-only audit records. It is intentionally not a storage backend or supervisor service.

## Contract

- Schema: `mojum.qa-supervisor.audit-chain/1`.
- Maximum chain length: 4,096 records.
- Exact fields: sequence, event kind, task key, state, reason code, timestamp, previous digest and digest.
- Sequence starts at one and is contiguous; timestamps are nondecreasing.
- Every SHA-256 digest covers canonical JSON of all record fields except the digest itself.
- Unknown fields, unknown enum values, malformed tokens, booleans in integer fields, broken links and digest mismatches fail closed.
- The returned report contains only schema, verification status, count, sequence bounds and terminal digest.

## Security and limitations

Use PUBLIC_SYNTHETIC fixtures only. This helper does not read SQLite, poll GitHub, access a queue, or verify source authenticity. SHA-256 chaining detects accidental or inconsistent edits but does not prevent an attacker from rewriting the entire chain. It is not a digital signature, trusted timestamp, append-only guarantee, approval, or merge authorization. Do not include PR bodies, tokens, host identifiers, PRIVATE Noosphere data or arbitrary payloads.
