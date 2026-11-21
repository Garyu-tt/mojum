# Streaming journal replay (R0.1, synthetic CPU fixture)

Status: implemented on a feature branch, pending independent review. This is
**not** an authenticated history, a signed snapshot or unbounded operation.

## Problem

The canonical Aquarium previously rebuilt state on every reopen by calling
`LocalNoosphere.dataset(limit=MAX_REPLAY_EVENTS)`, which decoded the entire
projection into one Python list. Its budget gate also called `count(*)` before
every append. With a 10,000-event ceiling a long-lived synthetic journal either
stopped or paid growing per-frame cost, and simply raising the ceiling would
have multiplied peak replay memory (issue #98).

## Change

- `LocalNoosphere.iter_events(...)` is a generator that runs the same SQL
  filter and visibility rules as `dataset()` but yields rows with bounded
  `fetchmany(batch_size)` calls (1–10,000, default 128). The cursor is closed in
  `finally`, including early generator close. It does not verify the hash chain
  by itself; callers must verify independently.
- `Aquarium._replay` keeps the existing full `verify_chain` pass and then
  reconstructs state from `iter_events` in batches. It checks the streamed
  event count and final hash against the verified count/head and re-checks the
  live journal head, so a concurrent append still fails closed.
- The pulse-summation fallback (`--pulse-sum auto`) re-streams the same verified
  history with the alternate arithmetic rule instead of reusing an in-memory
  list.
- The event budget uses a cached committed count updated only after a
  successful bootstrap, replay or CAS append. A stale writer is still rejected
  by the append's expected-head check.
- `MAX_REPLAY_EVENTS` is now an explicit **100,000** ceiling (still finite).
  `Aquarium.stored_events` exposes the verified/committed count so the CLI does
  not re-scan the journal.

## What this does not provide

- Memory during replay is bounded by the batch, but wall-clock CPU and disk
  use still grow linearly with the number of frames. There is no checkpointed
  fast-start or authenticated snapshot acceleration.
- The hash chain is unauthenticated: a local attacker who can rewrite the file
  can rewrite the chain. Trusted external anchors remain optional caller input.
- No encryption, distributed replication, background Android service or
  physical actuation is involved.

## Evidence

- `tests/test_streaming_replay.py`: iterator/dataset parity (content, order,
  filters, batch sizes), invalid-argument refusal, early-close safety, a replay
  that asserts `dataset()` is never called, cached-count budget fail-closed,
  stale-writer CAS, and the finite-ceiling constant.
- `tests/stress_streaming_replay.py` (not auto-discovered): builds a real
  synthetic journal above the legacy 10,000-event limit, cold-reopens it with
  `dataset()` disabled, appends one more frame, and prints counts and timings.
  Run with:

  ```sh
  PYTHONPATH=src python3 tests/stress_streaming_replay.py
  ```
- The full `unittest discover -s tests` regression and the CPython
  3.11/3.12/3.13 CI matrix exercise the same paths.

## Boundaries kept

Policy schemas, bootstrap manifests, frame payloads and existing journal bytes
are unchanged, so historical journals replay identically. No goal, reward,
pain, affect or obstacle oracle is introduced. Issue #98's broader
authenticated-segment and indefinite-lifetime goals remain open.
