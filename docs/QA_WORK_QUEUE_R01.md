# Mojum QA durable work queue R0.1

mojum.qa-work-queue/1 is a CPU-first storage primitive for issue #1179. It is not a deployed supervisor daemon and has no GitHub, model-provider, process-launch, dashboard, or merge authority.

## Contract

- Python 3.11+, standard library only; the caller supplies an explicit SQLite database path.
- File-backed databases use SQLite WAL and synchronous=FULL; each mutation is serialized with BEGIN IMMEDIATE.
- idempotency_key is unique. Repeating an identical key/kind/payload/retry budget returns the existing job. Reusing a key for different work raises IdempotencyConflict.
- States are queued, leased, succeeded, and failed. Retryable failures requeue until the attempt budget is exhausted.
- Claims receive opaque random lease tokens. The token is returned only in the `Lease` capability from `claim()`; `Job`, `get()`, `list_jobs()` and audit projections never disclose it. Heartbeat and completion require the current token and an unexpired lease. A reclaimed worker cannot mutate the job using an old token.
- The on-disk format is pinned by `qa_meta.schema_version`. Unknown versions and pre-existing unversioned queue tables fail closed; this release defines no automatic migration path. Back up and explicitly migrate data before changing the schema.
- Expired leases are requeued or failed according to the attempt budget; reclamation processes at most 500 expired rows per resident batch. Work runs outside SQLite transactions; a blocked job does not retain a global lock.
- Audit records contain event names, timestamps, worker labels and bounded error codes only. Payload contents are not copied into the audit log.
- Payloads are exact native JSON values with bounded depth, item count and UTF-8 size. The module performs no network calls, subprocesses, host discovery, environment lookups, or PRIVATE Noosphere access.
- Concurrent claims are serialized by SQLite transactions across independent connections and OS processes; the focused suite includes a multi-process exactly-once claim regression.

## Minimal use

```python
from mojum.qa_work_queue import QAWorkQueue

with QAWorkQueue("/var/lib/mojum/qa-queue.sqlite3") as queue:
    job, inserted = queue.enqueue(
        "pr:123:head-sha:triage-v1", "triage",
        {"pr": 123, "head_sha": "..."}, max_attempts=4,
    )
    lease = queue.claim("qa-worker-01", lease_seconds=120)
    if lease is not None:
        # Execute bounded work outside the SQLite transaction.
        queue.complete(lease.job.id, lease.token)
```

Queue state is not CI evidence or merge authorization. A caller must independently validate exact-head Actions runs, review state, permissions, base freshness and release policy. GitHub polling, dependency DAG, model triage, read-only dashboard and fix/merge executor remain separate follow-up slices.

## Verification

pytest -q tests/test_qa_work_queue.py covers idempotency, detached payloads, WAL persistence, concurrent thread/process claims, lease expiry/reclaim, stale-token fencing, retry exhaustion, bounded validation, audit provenance and filtering.
