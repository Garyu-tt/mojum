# Mojum QA Work Queue R0.1

An opt-in, local SQLite queue primitive for durable QA tasks. Python 3.11+ standard library only.

## Boundary

This module stores public pull-request metadata and queue lifecycle only. It is not a GitHub poller, a persistent daemon, a dependency-DAG evaluator, a model executor, a distributed consensus protocol, or a merge gate. It does not establish worker identity. The caller supplies a persistent database filename and a logical integer clock; no wall-clock calls are made.

Do not put source diffs, prompts, tokens, private Noosphere records, user data, or host identifiers in task payloads. Payload keys are limited to `source`, `reason`, and `workflow`, with bounded string values.

## API

- `enqueue(...)`: insert a task once by unique idempotency key. A duplicate key returns the original task without mutating its original metadata.
- `claim(...)`: atomically claim the oldest queued task or reclaim the oldest expired lease. SQLite `BEGIN IMMEDIATE` serializes concurrent claimers.
- `heartbeat(...)`: extend a lease only when owner, opaque lease token, and unexpired lease all match.
- `finish(..., success=True|False)`: transition a live lease to `done` or `failed`.
- `release(...)`: return a live lease to `queued`, preserving attempt count.

Each operation opens and closes its own connection. WAL mode, FULL synchronous mode, foreign keys and a bounded busy timeout are enabled. Database path ownership and filesystem permissions remain the caller's responsibility. WAL is not a substitute for backups or multi-device replication.

## Lease semantics

All times are caller-supplied integer logical milliseconds. Lease validity is half-open: a lease is valid while `now < lease_expires_at`; at equality it is expired. Expired leases can be reclaimed. A reclaimed lease receives a different token, so an old worker cannot heartbeat, finish, or release it. Lease tokens are concurrency capabilities, not cryptographic worker authentication.

## Validation and limits

Schema version is fixed at 1; unknown versions fail closed. Task IDs are SQLite-local integers. PR numbers are positive exact integers, SHA values are lowercase 40-hex, keys/owners/kinds are bounded ASCII identifiers, and payloads are a small allowlisted flat string map. Boolean values are rejected where integers are required. The database must be a caller-owned persistent SQLite filename, not an in-memory or URI database.

## Test

```sh
PYTHONPATH=src python -m unittest tests.test_qa_work_queue -v
```

The focused GitHub Actions workflow tests Python 3.11, 3.12 and 3.13. Independent QA and exact-head CI are required before merge. No production service is deployed by this change.
