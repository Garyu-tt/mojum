# Spartan delegated executor contract R0.1 (#701)

**Status:** optional, offline reference contract only. Does **not** install, import, connect
to, or benchmark Ray/Temporal; no second scheduler, model execution, PR
publication, independent QA, or production deployment is included.

## Why a separate contract?

The accepted coordinator in `tools/spartan_coordinator.py` already owns worker
slots, scope admission, task state, lease expiry, retry budget (maximum 3), and
attempt-based fencing. A Ray task or Temporal workflow must be a **subordinate
executor**, not another owner or a way around Cezar/Spartan admission. A
coordinator attempt has no UUID: this adapter derives a stable SHA-256
`dispatch_id` from version, task ID, worker ID, attempt. This is an idempotency
**routing key**, not authentication, HMAC, a lease token, or randomness.

- `tools/spartan_delegate.py`: opt-in side tables `delegated_jobs` and
  `delegated_pr_intents` in the **same Spartan SQLite** transaction domain.
  This avoids a second authority and an unsafe cross-database commit.
  Existing `tasks` and all Noosphere/GENESIS/Graph journals are untouched.
- `submit`: `BEGIN IMMEDIATE` check against a current running task row, exact
  worker/attempt and live lease; idempotent once per attempt.
- `start`, `cancel`, `report`: re-check the live authoritative lease inside
  the same SQLite write transaction. Terminal delegated states never revive.
  Cancellation is a **local acceptance gate**; actual Ray/Temporal interruption
  would be best-effort and must be reconciled.
- `reconcile`: orphan pending/running delegates after task expiry, cancellation,
  worker replacement or host loss; does not rewrite coordinator tasks.
- `prepare_pr_intent`: one durable `task_id` publication reservation across
  all attempts, after receiving only a SHA-256 digest for a reported artifact.
  On retry, an existing intent returns `False` and must trigger an independent
  GitHub query by dispatch ID, never a blind second POST. This is **not**
  guaranteed exactly-once remote GitHub publication; the unknown outcome after
  a crash is intentionally unresolved until external reconciliation.
- `Ticket.routing_packet('ray'|'temporal')` exports only version, hashed
  dispatch identity, attempt and deadline. No task prompt, raw payload, source
  tree, PRIVATE history, host, worker key or GitHub token is emitted.

`create_schema(db)` is an opt-in action on the **Spartan coordinator**
database, not a migration of canonical Aquarium memory. Do not invoke on
production until owner approval and backup. The caller is responsible for
authenticating the worker and mapping `dispatch_id` to an authorized, isolated
worktree; the routing packet alone grants **no execution permission**.

## Offline exercise

Run from the repository root with Python 3.11+ and no third-party dependencies:

```sh
python3 -m unittest -v tests.test_spartan_delegate
python3 -m tools.spartan_delegate_bench
```

Example using an already initialized, owner-private Spartan DB and *one live
claim*:

```python
from tools import spartan_coordinator as sc
from tools import spartan_delegate as delegated

db = sc.connect("spartan-state.sqlite3")
try:
    delegated.create_schema(db)  # explicit opt-in
    lease = sc.claim(db, worker_id=1, now=110, ttl=900)
    if lease is not None:
        ticket = delegated.submit(db, lease["id"], 1, lease["attempts"], 111)
        packet = ticket.routing_packet("fixture")  # safe fake backend only
        delegated.start(db, ticket, 112)
        # after an isolated executor produced a verified artifact:
        delegated.report(db, ticket, 113, "a" * 64)  # fixture digest only
        delegated.prepare_pr_intent(db, ticket, 114, "a" * 64)
        # NO external PR is posted here; independent QA still required.
finally:
    db.close()
```

The synthetic example uses **fixed virtual times**, only valid for this fixture.
Actual adapters must use coordinator clock/lease time with authenticated worker
identity, and must call coordinator `heartbeat(..., attempt=...)` to renew.
Never accept client-supplied timestamps as proof of authority in a real service.

## Mapping to optional external adapters (not yet active)

| Boundary | Ray candidate | Temporal candidate |
| --- | --- | --- |
| Dispatch | one uniquely labeled, resource-limited remote task | workflow/activity with stable workflow ID and bounded retry policy |
| Routing | `dispatch_id` in task input and structured results | `dispatch_id` as workflow ID / activity idempotency key |
| Retry | disable unbounded native auto-retries, reconcile with lease first | activity retry must be fenced at each attempt, and replay-safe |
| Cancellation | cancel reference best-effort; reject old result at ledger | request cancellation best-effort; reject old result at ledger |
| Work recovery | Ray object/actor loss becomes orphan until re-admitted | Temporal may resume activities, but only after fresh lease check |
| Artifact | digest only; no GitHub access | digest only; no GitHub access |

**Security boundary:** no network URL, subprocess, credentials or external
package load exists in this PR. The owner must approve isolated workers, mutual
authentication, process/memory/network limits, sanitized trace records, exact
dependency versions and real fault-injection tests before a backend adapter
can be enabled. Ray task replay and Temporal activity retry can repeat effects,
so external publication requires the separate durable intent and a GitHub
reconciliation key before any attempt. Only independent QA can approve/merge.

## Resource and compatibility gates

`spartan_delegate_bench.py` runs 300 real coordinator claims and 300
local delegate transitions in a single **in-memory** SQLite database. It
outputs elapsed seconds, peak Python `tracemalloc` bytes and intent count;
it excludes Ray, Temporal, models, process RSS, disk and network. It is a
reproducible microbenchmark, **not** evidence of 300 live model sessions.
Measure wall/CPU/RSS/disk under a representative persistent DB and real
sandboxed workloads before adopting Ray or Temporal. A simpler SQLite
executor remains the default if there is no measured benefit.

Known risk: a local process can call these functions if it can access the
coordinator SQLite. The DB's existing owner-only permissions and
`spartan_coordinator.connect` filesystem guards remain mandatory. This
contract is not multi-host single-writer consensus, authentication, or a
replacement for process isolation.

## Upstream provenance and license gate

- Ray: <https://github.com/ray-project/ray> (Apache-2.0 upstream source;
  see upstream LICENSE and docs). 
- Temporal server: <https://github.com/temporalio/temporal> (MIT upstream
  source; see upstream LICENSE). Its SDK, image, dependencies and any hosted
  services need separate licensing and data-retention review.
- Ray retry behavior: <https://docs.ray.io/en/latest/ray-core/tasks.html>.
- Temporal activity idempotency guidance: <https://docs.temporal.io/activities>.

No upstream dependency is selected or pinned in the runtime, because none is
installed or executed. A follow-up backend PR must pin a vetted version by
digest, audit transitive licenses, and provide isolated 2-host partition/retry
and cost-vs-SQLite measurements. Registry status `planned` and
`license_review=required` must remain unchanged pending independent QA.
