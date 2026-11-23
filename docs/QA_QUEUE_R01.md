# QA durable task queue R0.1

## Scope

`mojum.qa_queue.DurableTaskQueue` is the first isolated implementation slice
for issue #1179. It is a stdlib-only SQLite work queue intended to be embedded
by a future supervisor. It does not poll GitHub, invoke a model, launch workers,
deploy a service, inspect PR checks, or authorize merges.

Use a dedicated local file path. File-backed SQLite is required because each
operation opens an independent connection; `:memory:` is deliberately
rejected rather than silently losing state between operations. The database
contains queue payloads and audit metadata, so callers must not enqueue
secrets, PRIVATE Noosphere records, credentials, or raw private diffs.

## Contract

- SQLite WAL mode, foreign keys, busy timeout and bounded JSON payloads.
- Every enqueue/claim/lease transition uses an explicit `BEGIN IMMEDIATE`
  transaction. Independent processes serialize ownership changes through
  SQLite's writer lock.
- Idempotency key is unique. Repeating the same key with the same task key and
  canonical payload returns the original task; reusing it for different
  content raises `DuplicateTaskError`.
- Claims select the oldest eligible queued task or a task whose lease has
  expired. The claim increments attempts and issues a fresh unguessable lease
  token. Reclaiming an expired lease records `lease_expired`.
- Heartbeat, completion and failure require the exact worker ID and lease
  token and reject expired leases. This fences stale workers after recovery.
- Failure can be terminal or requeue at an explicit future timestamp.
- Append-only queue events record enqueue, claim, expiry, heartbeat, completion
  and failure transitions. Event rows contain metadata only, never payloads.
- Payloads are plain dictionaries serialized as canonical finite JSON, capped
  at 64 KiB by default. Event reads are capped at 1,000 rows.

## Minimal use

```python
from mojum.qa_queue import DurableTaskQueue

queue = DurableTaskQueue("./qa-supervisor.sqlite3")
task = queue.enqueue(
    "pr:123",
    "github:pr:123:head:abc123",
    {"pr_number": 123, "head_sha": "abc123"},
)
lease = queue.claim("worker-1", lease_seconds=90)
if lease is not None:
    # Do bounded work; heartbeat before the lease expires.
    queue.heartbeat(lease.task_id, "worker-1", lease.lease_token,
                    lease_seconds=90)
    queue.complete(lease.task_id, "worker-1", lease.lease_token)
```

Use a stable idempotency key derived from the source event identity, such as
repository + PR number + exact head SHA + work kind. Do not use a random key
for replayable discovery events.

## State model

```text
queued --claim--> leased --complete--> completed
                    |  \
                    |   +--fail(retry=False)--> failed
                    |
                    +--fail(retry=True)--> queued
                    |
                    +--lease expiry--> leased by a new worker/token
```

There is no automatic retry limit or dead-letter policy in this slice; the
supervisor must decide retry/backoff policy. Queue leases provide at-least-once
work delivery, not exactly-once side effects. Workers must make external
operations idempotent and revalidate their source revision before acting.

## Verification

Run the focused suite:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_qa_queue.py' -v
```

The test module covers idempotency, payload detachment, delayed availability,
lease expiry and stale-owner fencing, heartbeat, terminal/retry failure,
restart durability, concurrent independent workers, bounded inputs and audit
limits. Full repository CI and independent exact-HEAD QA are still required.

## Explicit limitations

- No task priority, cancellation, lease renewal background thread or worker
  process management.
- No distributed consensus or network-filesystem safety guarantee. Place the
  SQLite file on a local filesystem with SQLite locking semantics.
- SQLite WAL allows concurrent readers but still has one writer at a time.
- A crashed worker's task becomes reclaimable only when its lease expires.
- Wall-clock nanoseconds are used for expiry; hosts sharing the database must
  have sufficiently synchronized clocks.
- The queue is not a merge gate. A future supervisor must separately validate
  exact-head CI, review policy, branch protection, dependency DAG and explicit
  release authorization.
