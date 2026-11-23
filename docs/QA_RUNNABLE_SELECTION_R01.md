# QA runnable selection policy R0.1

## Purpose

This module is the deterministic scheduling-policy slice for Mojum QA supervisor
issue #1179. It ensures that a PR waiting for CI, lacking permission, blocked by
a conflict, or awaiting strong-model escalation does not prevent unrelated work
from being selected.

## API

`select_runnable_tasks(tasks, now_tick, limit=16)` accepts an exact list of
bounded task snapshots. Each task requires:

- `task_id`: unique printable ASCII identifier, at most 128 characters.
- `state`: one of the explicitly supported supervisor states.
- `priority`: integer 0..1,000,000; larger values run first.
- `sequence`: non-negative monotonic enqueue sequence; smaller values run first.

Optional fields:

- `not_before`: logical tick at which a retry becomes eligible (default 0).
- `dependencies`: up to 256 task IDs that must all have state `MERGED`.

The result is immutable and contains selected IDs, deferred IDs with reason
codes, and ignored active/terminal IDs. It never returns caller payloads.

## Scheduling contract

- Only `DISCOVERED` and `RETRY` are eligible.
- `WAITING_CI`, `BLOCKED_PERMISSION`, `BLOCKED_CONFLICT` and `ESCALATED`
  are deferred individually; they do not stop scanning.
- Active tasks (`CLAIMED`, `REVIEWING`, `TESTING`, `FIXING`, `RETESTING`)
  and terminal/non-schedulable tasks are ignored.
- Retry deadlines and dependency readiness are evaluated per task.
- A missing or non-merged dependency defers only its dependent task.
- Order is deterministic: priority descending, enqueue sequence ascending,
  task ID ascending. Items beyond the batch limit are reported as deferred.
- Unknown fields/states, duplicate IDs, malformed dependencies, booleans in
  integer fields, hostile container subclasses and over-budget input fail closed.

## Safety and integration boundary

This is a pure decision function. It does not claim a lease, write SQLite,
poll GitHub, inspect workflow runs, invoke a model, execute tests, deploy a
service, or authorize merges. The caller must atomically claim selected work
through the durable queue and re-check task state/dependencies at claim time.
The function is not a replacement for the queue's lease fencing or the
independent exact-head CI and review gates.

Python 3.11+ standard library only. No mandatory dependencies or network access.

## Tests

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_qa_runnable_selection -v
```

Coverage includes blocked-task non-interference, deterministic order regardless
of input order, logical retry deadlines, dependency gating, missing parents,
active/terminal states, escalation/conflict skips, batch limits, duplicate IDs,
unknown fields/states, exact native types, bool-as-int rejection and immutability.
