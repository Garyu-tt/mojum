# Spartan provider budget gate R0.1 (issue #478)

This is an **opt-in accounting primitive**, not a deployed coding worker, API
client, sandbox, or independent acceptance service. Nothing invokes a paid model
and nothing merges a PR.

## Contract

Before a model call, the adapter must bound all input + maximum output tokens
and price the **maximum possible charge** using its pinned provider/model
tariff. Units are **integer micro-USD**, not floating point dollars. Call:

```python
from mojum.spartan_budget import BudgetCaps, BudgetDenied, BudgetLedger

ledger = BudgetLedger(
    "/private/worker-control/model-budget.sqlite3",
    BudgetCaps(requests_per_minute=4, tokens_per_minute=8000,
               usd_micros_per_utc_day=1_000_000),  # $1 / UTC day
)
receipt = ledger.reserve(
    "task-123/attempt-1/provider-call-1", "model-version-pinned",
    tokens=1200, usd_micros=250,  # an upper bound, $0.000250
)
if receipt.duplicate:
    raise RuntimeError("Already reserved: retrieve prior result; do not call again")

# Only here may an isolated provider adapter send the request, with its own
# upstream token/output limits and timeout. Never expose the API key to tests.
# When an authoritative usage receipt is available:
ledger.record_usage("task-123/attempt-1/provider-call-1",
                    tokens=actual_tokens, usd_micros=actual_cost_micros)
```

The same idempotency key can be retried for **lookup only**, never as
permission to make another chargeable call. Different parameters under the same
key fail closed. Use a distinct key only for a legitimately new paid operation.
Reservations are durable, even if a process crashes or the provider times out.

- Atomic SQLite `BEGIN IMMEDIATE` admissions with WAL. All cooperating workers
  must use a **single shared SQLite file on a filesystem with working SQLite
  locking** and identical immutable caps. Separate replicas or files have
  independent limits and are **not a distributed global budget**.
- Request and token quotas use **fixed UTC 60-second buckets**; these are not a
  sliding-window guarantee. Daily money uses fixed UTC days. The number of
  calls across a bucket boundary can be higher than the nominal per-minute cap;
  configure the actual upstream provider limiter independently.
- Both the reserved cost and tokens stay debited after usage is reported,
  rather than trusting refunds, unsuccessful requests, or unclear network
  errors. An observed overrun freezes new admissions persistently. There is no
  automated unfreeze, refund, reconciliation, pruning, or cap migration in R0.1.
- A backward wall-clock jump denies new admissions. A changed policy on an
  existing database is rejected. Persist the SQLite file and its sidecars
  securely; losing it loses the accounting evidence.
- Do not store credentials, prompts, model responses, or raw task content in
  the ledger. The key is stored as SHA-256; model identifier, reserved ceilings,
  and provider-reported usage totals are stored in plaintext.
- The adapter must still implement provider-owned idempotency if supported,
  retries/Retry-After, precise tariff pricing, hard output limits, timeout,
  daily account spending limits and authenticated upstream cost reconciliation.
  **This module alone cannot guarantee actual provider spend.**

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_spartan_budget.py -v
```

Seven stdlib tests cover atomic quota caps, eight competing threads, restart
persistence, idempotency, bad input, backward time and overrun freeze. The
module imports no providers or credentials and makes no network requests.

The planned follow-up is to integrate this as a mandatory preflight of a
sandboxed provider adapter, enforce the real provider rate limits separately,
and run a staged 4/16/32 worker concurrency test before any larger ramp. This
does **not** authorize activating the 300-worker fleet.

## Private storage QA repair

The budget gate **fails closed on non-POSIX hosts and relative paths** pending a
platform-specific secure-store implementation. On Linux, use an absolute file
path inside a dedicated directory owned by the worker UID with mode 0700.
The directory must not be a symlink. Every ancestor must be owned by
root or the worker UID (even an 0755 foreign-owned directory is replaceable
by its owner); writable non-sticky ancestors are rejected.
Existing DB, WAL and SHM paths must be regular files owned by that UID, with
one link and no group/world permission bits. New files are precreated with
O_EXCL/O_NOFOLLOW and mode 0600 before SQLite connects, without changing the
process-wide umask. Unsafe existing files are **not** silently chmodded;
inspect them offline and explicitly migrate or restrict permissions first.
The parent-directory check protects against other UIDs, **not** an attacker
already running with the same UID. This remains a local SQLite ledger and not
an authoritative provider-spend limit or a substitute for #415 admission.
