# Ray / Temporal delegated execution boundary (R0.1, #701)

**Experimental and OFF by default. No new required dependencies.** The existing
`tools/spartan_coordinator.py` SQLite coordinator is the sole owner of task
leases, attempts, worker slots, expiry, state, retries and PR acceptance
handoff. This adapter does not replace Cezar, mutate Noosphere, grant access
to secrets, contact external services by default, or publish/merge a PR.

## Implemented

`tools/spartan_delegate.py` adds an explicitly installed side ledger **in
the same coordinator SQLite database**, where the original `tasks` rows
remain authoritative. A valid live `tasks` row, exact worker ID, and current
attempt are checked inside each `BEGIN IMMEDIATE` transaction.

* `DelegatedLease` and `mojum.spartan.delegation/1` transport only six
  allowlisted metadata fields, never arbitrary task payloads, prompts, paths,
  infrastructure credentials or organism data.
* `reserve()` permits only the **first** dispatch for a given task/attempt;
  duplicate reservation returns `False`. Timeout after reservation is
  ambiguous and **MUST NOT** cause an automatic resend.
* `dispatch_ray()` accepts only an administrator-provided trusted Ray
  remote-function handle and calls `.remote(bytes_packet)`.
  `dispatch_temporal()` accepts a configured Temporal Python client and
  calls `start_workflow("mojum_delegate_v1", bytes_packet, id=..., task_queue=...)`.
  Both are only invoked explicitly; no SDK import occurs in the baseline.
* `acknowledge()` records a small opaque external job ID, not successful
  execution. `request_cancel()` is **only a cancellation request**.
  `mark_orphans()` flags stale submissions; it cannot kill a detached
  remote job. The remote worker MUST revalidate coordinator authority
  before publication.
* `prepare_pr()` records **one immutable PR publication intent per task**
  with exact branch and 40-character HEAD SHA. Only its initial `True`
  permits a trusted external publisher to attempt GitHub publication.
  Ambiguous network errors leave `prepared` so no second automatic
  publication is permitted; `confirm_pr()` records a separately verified
  URL after manual reconciliation, including after lease expiry.

The ledger alone **does not deliver globally exactly-once GitHub writes**.
All publishers must participate in this gate. An independent QA principal
must verify real PR HEAD and base, required CI, approvals and compatibility.
No merge authorization is added here.

## Offline proof

Run from repository root:

```sh
python3 -m unittest discover -s tests -p 'test_spartan_delegate.py' -v
```

The tests use the real Spartan coordinator and injected fake Ray/Temporal
handles, no cluster and no network. Scenarios cover strict packet decoding,
payload isolation, duplicate dispatch, conflicting external acknowledgements,
expired lease and attempt-2 re-claim, orphan detection, cancellation intent,
exact-HEAD publication fencing, old-attempt collision, and durable SQLite
reopen after a simulated crash. They do **not** prove that a real remote
cluster, PR publisher or Cezar deployment is operational.

## Operator-only integration sequence

1. Open the coordinator database using its existing owner-only file/path
   validation, create its native schema, and explicitly call
   `spartan_delegate.install(db)`. Never use a separate live SQLite copy.
2. Claim with `spartan_coordinator.claim` and create a `DelegatedLease`
   from the actual `id`, `worker_id` and `attempts` plus an
   administrator-approved opaque reference.
3. Call exactly one Ray or Temporal dispatcher adapter using a trusted
   remote function/client and a bounded approved task queue.
   If a call fails ambiguously, **do not retry** without reconciling
   remote execution and coordinator state.
4. Record backend receipt through `acknowledge`; handle heartbeat,
   cancellation and expiration through Cezar/coordinator, separately
   from the delegated runtime.
5. Before any PR create, the independent authenticated publisher checks
   `prepare_pr`; after uncertainty it searches actual GitHub PRs by exact
   branch and SHA instead of creating another PR. Never send GitHub write
   tokens to model workers.

## Upstream assessment and limits

| Candidate | Versioned API reference | Upstream licensing | Status |
| --- | --- | --- | --- |
| Ray | Python Ray 2.60.0 docs: remote function `.remote` | Apache-2.0 | Not installed; optional |
| Temporal Python SDK | `Client.start_workflow`, SDK 1.x | MIT SDK; server deployment separately reviewed | Not installed; optional |

References: https://docs.ray.io/en/latest/ray-core/tasks.html ,
https://github.com/ray-project/ray/blob/master/LICENSE ,
https://docs.temporal.io/develop/python/client/temporal-client ,
https://github.com/temporalio/sdk-python/blob/main/LICENSE .
Reviewed 2026-11-23. The release/dependency/license matrix is not approved
for deployed services; an actual trial must pin exact wheel/server versions,
dependencies, credentials, endpoint trust and license terms **before** installing.
The core `pyproject.toml` remains stdlib-only.

Suggested **future** controlled experiment (NOT measured): one remote worker,
60 seconds, at most 256 MiB RSS, no GPU or Internet for the test job;
then evaluate one/two/four-worker latency, peak RSS/CPU, startup/operating
overhead, cancellation, partition, duplicate scheduling, unknown outcomes
and accepted PRs per hour versus the local coordinator. Reject a backend
that adds cost without an acceptance-throughput gain.

No claim of 300 active processes, zero remote duplicates under partitions,
live multihost failover or production deployment. An expired attempt may
still execute compute; it must never be treated as a trusted publisher.
