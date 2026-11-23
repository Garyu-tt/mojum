# QA supervisor restart recovery contract R0.1

Schema: mojum.qa-recovery/1 (wire identifier: mojum.qa-recovery/1).

This is a pure-Python offline decision helper for a supervisor that has loaded
a bounded task snapshot from its durable queue. It proposes actions only. The
queue adapter must separately perform a lease-fenced transactional compare-
and-swap before changing any row.

## Input

plan_recovery(tasks, observed_at=...) accepts at most 500 exact built-in task
objects and 4,000 dependency edges. Each task has exactly these fields:

- task_id: unique bounded non-empty identifier.
- state: DISCOVERED, CLAIMED, RUNNING, RETRY, WAITING_CI,
  BLOCKED_PERMISSION, BLOCKED_CONFLICT, SUCCEEDED, FAILED, or CANCELLED.
- attempt, max_attempts: native integers bounded to 1,000.
- lease_expires_at: non-negative integer for CLAIMED/RUNNING, otherwise null.
- not_before: non-negative integer logical timestamp.
- dependencies: list of task IDs in this same snapshot.

Times use one caller-selected logical integer-second domain. The module never
reads wall-clock time, touches SQLite, performs I/O, or imports providers.

## Decisions

- KEEP_TERMINAL: terminal state remains unchanged.
- KEEP_LEASE: active lease has not expired.
- REQUEUE: lease expired and retry budget remains.
- MARK_FAILED: lease expired and retry budget is exhausted.
- DEFER: external gate, incomplete dependency, or retry not yet due.
- BLOCK_DEPENDENCY: dependency failed or was cancelled.
- READY: dependencies succeeded and task is due.

A lease expires at lease_expires_at <= observed_at. Cycles, unknown dependencies,
duplicate IDs, inconsistent lease/state combinations, hostile container
subclasses, booleans in integer fields, and over-budget inputs are rejected.

## Safety boundary

The SHA-256 digest is an unkeyed consistency witness, not authentication.
A recovery decision is not a queue mutation, lease renewal, task execution,
CI approval, review, or merge authorization. WAITING_CI and permission/conflict
blocks are never automatically retried. The caller must re-read the
authoritative queue row and enforce its fencing token when applying an action.

## Reproduction

PYTHONPATH=src python -m unittest discover -s tests -p 'test_qa_recovery.py' -v

Python standard library only. No new mandatory dependencies.
