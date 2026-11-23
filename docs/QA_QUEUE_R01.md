# Durable QA queue R0.1 (#1179)

## Delivered slice

`mojum.qa_queue.DurableQueue` is an offline, stdlib-only SQLite work queue.
It is intentionally limited to durable scheduling primitives. It does not
poll GitHub, inspect CI, infer dependencies, invoke a model, deploy a service,
or authorize a PR merge.

- File-backed SQLite with WAL and FULL synchronous mode.
- Versioned schema metadata and a bounded canonical JSON payload (256 KiB).
- Unique idempotency keys: a retry returns the original job without replacing
  its payload.
- Atomic `BEGIN IMMEDIATE` lease acquisition, ordered by insertion ID.
- Lease heartbeat and completion/block transitions require the current live
  owner; expired or reassigned workers cannot renew or finish a job.
- Expired claims are returned to the ready queue by the next claimant.
- Explicit BLOCKED state is excluded from ready selection; a blocked PR cannot
  starve independent ready work. An operator can explicitly unblock it.
- Aggregate counts expose no payload contents.

## Minimal use

```python
from mojum.qa_queue import DurableQueue

queue = DurableQueue("./qa-supervisor.sqlite3")
queue.enqueue("pr:123:head:012345...", {"pr": 123, "head_sha": "012345..."})
job = queue.claim("worker-1", lease_seconds=90)
if job is not None:
    # Perform bounded, non-authoritative QA work here.
    queue.heartbeat(job.id, "worker-1", lease_seconds=90)
    queue.complete(job.id, "worker-1")
```

If the worker encounters a permission or external-service blocker, it should
call `block(job.id, worker_id, reason)` and continue claiming other work.
Blocked jobs require explicit `unblock(job_id)` after the cause is resolved.

## Operational boundaries

- Keep the database on a local filesystem with reliable SQLite locking. Do not
  assume a network filesystem is safe for concurrent SQLite writers.
- Use a unique worker identity per process and heartbeat well before expiry.
- The queue stores metadata and bounded JSON only; do not enqueue PRIVATE
  Noosphere records, secrets, tokens, hardware identifiers, or raw model
  contexts.
- SQLite WAL is durable local coordination, not a multi-host consensus
  protocol. A future distributed deployment needs an explicit shared lease
  authority or per-host queue partitioning.
- Queue completion is not a CI pass, QA approval, or merge authorization.
  Release decisions remain subject to exact-head deterministic gates.

## Verification

Run the offline stdlib suite:

```sh
PYTHONPATH=src python -m unittest tests.test_qa_queue -v
```

The supervisor's poller, dependency DAG, exact-head CI collector, read-only
dashboard, and service deployment are intentionally separate follow-up slices.
