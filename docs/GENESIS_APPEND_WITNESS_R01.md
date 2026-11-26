# GENESIS append-only continuity witness R0.1

This isolated, dependency-free, CPU-only helper compares **two existing**
`mojum.genesis.ledger/1` snapshots and proves that the second is an exact
append-only continuation of the first. It is neither a new writer nor a GENESIS
schema migration. It does not change the canonical GENESIS module.

```python
from mojum.genesis_append_witness import compare_append_only
report = compare_append_only(before, after, classification="PUBLIC_SYNTHETIC")
```

**Admission:** both native JSON trees are recursively type-checked, detached,
UTF-8 serialized and bounded to 128 events and 256 KiB per snapshot before
canonical `GenomeLedger.from_snapshot` replay. Booleans, floats, user-defined
container/string subclasses, unknown fields, invalid revision hashes, reordered
or changed historical acquisitions, seed rewrites and rollbacks fail closed.
The smaller 128-event cap is intentional: canonical prefix replay is bounded but
nonlinear, and this audit must not accept unbounded untrusted history. A larger
snapshot requires an explicitly reviewed streaming audit design.

**Output:** immutable inherited seed hash, both GENESIS revision hashes,
event counts, appended per-gene counts and delta sums, and SHA-256 commitment to
ordered appended event IDs. Original event IDs, `source`, `evidence`, event
timestamps and raw genome contents never appear in the report. An identical
pair receives `relation=equal`; a true continuation receives `append_only`.

**Trust boundary:** caller must classify data `PUBLIC_SYNTHETIC`. This is an
explicit API policy assertion, **not** automatic verification of consent or
privacy. Never feed real or PRIVATE Noosphere/organism data. Revision hashes
only detect accidental/unsanctioned content changes relative to the supplied
snapshots; an attacker can forge both snapshots and valid hashes. It is **not**
an owner signature, cryptographic log anchoring, source authentication, external
world/brain measurement, physical biology or durable two-copy failover proof.

**Tests:** `PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_append_witness.py' -v`

Related: parent #4, existing initial GENESIS contract and independent timeline
attestation (#847); this witness checks *cross-snapshot prefix continuity*,
not phenotype change at a tick.
