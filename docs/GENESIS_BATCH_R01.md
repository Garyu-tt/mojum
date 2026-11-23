# GENESIS acquisition batches R0.1

## Purpose

`mojum.genesis_batch.apply_acquisition_batch` is a small offline transaction planner for synthetic acquisition events. It validates and applies a batch to a **detached copy** of an existing `GenomeLedger` snapshot. A result is returned only if every acquisition passes the canonical GENESIS ledger rules.

This avoids partially updating a caller-owned in-memory ledger when a later event fails.

## Example

```python
from mojum.genesis import GenomeLedger
from mojum.genesis_batch import apply_acquisition_batch

ledger = GenomeLedger(seed)
result = apply_acquisition_batch(
    ledger.snapshot(),
    [event_a, event_b],
    expected_base_revision=ledger.revision,
)

# The result holds a detached final snapshot and one revision receipt per event.
new_snapshot = result.snapshot()
assert result.event_count == 2
assert result.final_revision == new_snapshot["revision"]
```

## Contract

- Input snapshot and events must be exact native JSON-shaped dictionaries/lists with bounded depth, node count and signed 64-bit integer values.
- Compact canonical JSON size is preflighted before serialization, including escaped strings/keys and structural bytes. Batch size is 1..64; total input JSON is at most 512 KiB; each event is at most 8 KiB.
- The detached planner accepts at most 128 pre-existing ledger events, below the storage ceiling of 1024. This intentionally bounds replay CPU: restoring a snapshot calls `GenomeLedger.append` for each event, and each append re-hashes the growing history and rechecks every effective tick prefix.
- Base snapshot revision is checked against an explicit expected revision.
- Each event is applied in order through `GenomeLedger.append`, using the immediately preceding revision as its CAS expectation.
- Existing GENESIS rules remain authoritative: event identity, genome match, gene bounds, recorded-time monotonicity, effective valid-from prefixes and duplicate rejection.
- Failure raises `GenesisError`; no caller-owned input or ledger is modified.
- Success returns a frozen receipt with immutable revision tuple and canonical serialized snapshot. `snapshot()` decodes a fresh detached copy each time.
- The batch SHA-256 is a deterministic consistency digest, not a signature or authentication proof.

## Limits and non-goals

This is **not** a database transaction, distributed commit protocol, event deduplication service, durable journal, encryption layer, or runtime integration. It does not make the existing `GenomeLedger` persistent or modify its schema. Inputs are PUBLIC_SYNTHETIC test fixtures only; no PRIVATE Noosphere data, physical biology, neural training, actuator state, hardware, or network is accessed.

## Reproduction

From the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_batch.py' -v
```
