# R0.14 / Local Cell v2 — replay failure-injection QA

This is QA for experimental [#115](https://github.com/Garyu-tt/mojum/pull/115)
and *does not* activate a production Dante, change a real Noosphere
database, or enable physical actuators.

## Verification contract

All tests use unique disposable temp-file PRIVATE SQLite databases and
canonical same-room synthetic clock. The test matrix runs on CPython
3.11, 3.12 and 3.13, without any external dependency:

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_aquarium_cell_policy_v2*.py' -v
```

Cases:

1. Fail **before** SQLite append: no event, RAM movement, learned policy
   or journal head changes. Cold restart yields the original checkpoint.
2. Simulate process interruption **after** successful SQLite CAS commit
   but **before RAM adoption**: the running instance refuses a stale
   checkpoint, while a cold restart reconstructs the one durable v2
   experience and safely continues from tick 1 to tick 2.
3. Two Aquarium instances on the same disposable DB: the stale writer
   cannot win the event head compare-and-swap or publish its old policy
   snapshot, and a fresh reader recovers the new committed state.
4. External journal head mismatch is rejected; a synthetic event
   manually corrupted at the SQLite level fails hash-chain verification
   on restart. Hash-chain alone is **not authenticated against an
   attacker who rewrites the entire journal**.
5. Two independent, equally initialized local SQLite aquariums produce
   identical causal frame records, policy state and replay at tick 65.
6. The strict `MAX_CELLS=512` policy limit is exercised with 513
   distinct proprioceptive cell visits. At capacity, v2 refuses further
   new-cell outcome accumulation with an explicit error and no partial
   policy mutation. **This is a P1 long-running availability limitation**,
   not acceptable for an always-on organism without a future
   independently versioned bounded forgetting/eviction policy.
7. Snapshots and restored model records must not alias mutable caller
   objects.

## Interpretation / limitations

These tests do not emulate literal power cuts, filesystem durability
loss, process killing between *individual* SQLite PRAGMA statements,
Windows/Android OS file-sharing behavior, cryptographic signatures,
rollback attacks, or Byzantine/fork reconciliation. The injected
post-commit exception is a controllable proxy for a known interruption
window.

This QA intentionally **does not alter the already pinned**
`mojum.developmental.local-cell/2` algorithm or its persisted
interpretation. A future retention policy must use an explicit
new schema and upgrade path; never silently change an old journal
during replay. Keep all PRs Draft until owner/reviewer acceptance.
