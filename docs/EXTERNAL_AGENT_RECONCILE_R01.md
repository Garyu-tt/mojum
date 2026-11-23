# External agent / GitHub correlation R0.1 (#717)

This **offline, stdlib-only projection** joins authenticated external-agent
ReceiptJournal.readback() data (separate draft PR #792) to an independently
fetched GitHub issue/PR metadata snapshot and optional externally known schedules.

The adapter **does not authenticate receipts or GitHub**: callers must do that
before passing the data. It neither queries GitHub, schedules jobs, mutates the
journal, nor accesses production Cezar or Noosphere. Results are PRIVATE
operational metadata, not a public status endpoint.

## Integration contract

Import mojum.external_agent_reconcile.reconcile and
mojum.external_agent_reconcile.GITHUB_SCHEMA. Pass the authoritative
ReceiptJournal.readback(observed_ms=...) result as first argument.

The optional second argument is an exact-key GitHub snapshot:

    {
      "schema": "mojum.github-correlation-snapshot/1",
      "repo": "Garyu-tt/mojum",
      "observed_ms": 999900,
      "complete": false,
      "issues": [{"number": 717, "state": "open"}],
      "pulls": [{"number": 792, "state": "open", "merged": false,
                 "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]
    }

Optional schedules are list entries with exactly source, agent_id and repo,
for example: {"source":"chatgpt","agent_id":"workerA","repo":"Garyu-tt/mojum"}.

Output contains source, agent_id, run_id, server-observed state and age,
separate issue/PR correlation, exact SHA-match flag, contradiction warnings,
counts of confirmed receipt statuses, plus UNKNOWN schedule-only records.

## Invariants

- Only authenticated receipt readback can report active, stale, finished or
  failed agents. A PR, commit, schedule, reservation, Telegram notification or
  Cezar local parallel limit can never synthesize an active worker.
- A merged PR cannot terminate an active heartbeat; it produces a warning.
  A mismatched commit SHA is flagged, not silently reconciled.
- Missing GitHub objects are **unknown**, not deleted or proof of absence;
  GitHub metadata older than 300000 ms is stale and not correlated.
- ReceiptJournal's readback truncation flag is retained explicitly. Counts
  are partial when truncated, so absence from the list proves nothing.
- Strict JSON-compatible native type and range checks reject duplicate runs,
  duplicate issue/PR rows, timestamps from the future, inconsistent TTL and
  terminal status, mismatched repository names, malformed IDs/SHAs, oversized
  collections, surprising extra fields and boolean-as-integer tricks.
- Correlation generates canonical GitHub links only from validated repository
  names and positive integer IDs. No arbitrary source URLs are emitted.
- Limits: 1000 runs, 1000 schedules, 2000 issues, 2000 pulls; CPU/stdlib-only.

## Tests

From the repository root:

    PYTHONPATH=src python -m unittest discover -s tests -p test_external_agent_reconcile.py -v

Hermetic fixtures cover live, stale, schedule-only, completed and failed
workers, merged PR conflicts, cross-repository isolation, SHA mismatch,
unavailable/stale GitHub, eight simultaneous external workers, partial views,
and malicious/oversized data. This is not proof of actual external liveness.

## Remaining acceptance

No live authenticated ingress, webhook validation, production GitHub API
client with pagination/rate-limit handling, real key rotation, Cezar dashboard
or Telegram wiring, source-owner verification, production deployment or
independent PR approval is claimed. This is one independent unmerged slice of
open issue #717, and requires exact-head CI plus independent QA before merge.
