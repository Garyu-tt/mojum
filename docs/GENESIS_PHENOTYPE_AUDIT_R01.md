# GENESIS phenotype snapshot auditor R0.1

## Purpose
An opt-in, read-only consistency checker for a caller-supplied GENESIS GenomeLedger snapshot and a claimed phenotype snapshot. It restores the existing mojum.genesis.ledger/1 through GenomeLedger.from_snapshot(), recomputes the phenotype at the supplied tick, and compares every field.

## Usage
```python
from mojum.genesis_phenotype_audit import audit_phenotype_snapshot
report = audit_phenotype_snapshot(ledger_snapshot, phenotype_snapshot, source_classification="PUBLIC_SYNTHETIC")
assert report.accepted
print(report.to_dict())
```

## Admission and resource limits
- Both inputs must be exact built-in dictionaries containing JSON-native data.
- Source classification must be explicitly PUBLIC_SYNTHETIC.
- Maximum compact canonical JSON size: 1 MiB per input, preflighted before serialization including quoted/escaped string values, object keys, separators and scalar representations.
- Maximum ledger events for this auditor: 128. This is stricter than the GENESIS ledger storage limit because replay revalidates every effective tick prefix while rebuilding the append-only history; the audit cap bounds CPU cost before serialization, copying or ledger restoration.
- Integers are restricted to the signed 64-bit range before conversion or serialization; non-finite floats and unpaired Unicode surrogates are rejected.
- Maximum nesting depth: 24; maximum traversed nodes: 20,000 per input.
- Strings are UTF-8 encodable and at most 16,384 characters.
- Non-finite numbers, custom mappings/containers, non-string keys and unsupported objects are rejected.
- Ledger schema, event count, revision and acquisition rules are delegated to GenomeLedger.from_snapshot().

## Report semantics
`accepted` is true only when the supplied phenotype has exactly the same keys, JSON value types and values as the recomputed projection. Nested objects and arrays are compared recursively without Python's bool/int or int/float equality coercions. `mismatch_fields` reports field names only. SHA-256 values cover canonical computed and supplied phenotype objects. The report contains no raw event IDs or parameter values.

## Security and limitations
This is a deterministic consistency check over caller-supplied synthetic data. It is not authentication: SHA-256 is a digest, not a signature. Caller-provided PUBLIC_SYNTHETIC is an assertion, not independently verified provenance. It does not prove biological accuracy, learning, cognition, physics or live runtime state. It does not read files, SQLite, Noosphere, networks, hardware or process state; it performs no writes. It adds no mandatory dependencies and does not change Aquarium behavior.

Require exact-head Python 3.11–3.13 CI and independent review before considering integration.
