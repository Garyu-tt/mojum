# QA supervisor durable queue (R0.1)

Issue #1179. This is the durable scheduling primitive, not the complete supervisor.

## Guarantees

- Python 3.11+ standard library only; SQLite WAL and FULL synchronous commits.
- Idempotent enqueue by caller key, defaulting to issue number plus full head SHA.
- Every claim uses a random lease token and expiry. Heartbeats require the current
  live token. Expired active leases return to discovery on the next claim, including
  after process restart.
- Claims use BEGIN IMMEDIATE, serializing competing processes before selecting work.
- Explicit state transitions. Moving to ready, waiting_ci, blocked, escalated, or
  merged releases the lease. A separate supervisor/poller must decide when to
  requeue those states; pending CI never blocks unrelated discovered jobs.
- Payload is bounded to 64 KiB of finite JSON metadata. Never store credentials,
  private Noosphere records, raw diffs, prompts, or host identifiers here.

## API sketch

```python
queue = DurableQAQueue("/var/lib/mojum/qa-supervisor.sqlite3")
queue.enqueue(1177, "<full 40-character head SHA>", {"source": "github"})
job = queue.claim("worker-01", lease_seconds=120)
if job:
    queue.heartbeat(job.job_id, job.lease_token, lease_seconds=120)
    queue.transition(job.job_id, job.lease_token, "reviewing")
```

This slice does not implement GitHub polling, PR dependency DAGs, exact-head CI
verification, model execution, dashboard, service deployment, or merge authorization.
No production service is deployed by this change.

## Verification

From a checkout: `python -m unittest tests.test_qa_supervisor_queue -v`.
Tests cover idempotency, restart persistence, concurrent exclusive claims,
lease expiry/heartbeat, state transitions and hostile/bounded payload values.
