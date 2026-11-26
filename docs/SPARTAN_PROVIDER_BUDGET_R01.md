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
  existing database is rejected, including runtime mutation of the public
  `ledger.caps` attribute: persisted SQLite limits remain authoritative on
  every admission. Persist the SQLite file and its sidecars
  securely; losing it loses the accounting evidence.
  On POSIX the ledger requires an absolute file path inside an owner-only
  0700 parent, rejects replaceable ancestors and symlink paths, and rejects
  preexisting DB/WAL/SHM files unless regular, owner-only, and single-link.
  New databases are precreated with mode 0600. Non-POSIX platforms and hosts
  without O_NOFOLLOW are denied until a secure implementation is available. Existing
  unsafe ledgers are **not** chmod-repaired automatically; inspect and migrate
  them explicitly before retrying. `:memory:` and SQLite URI paths are refused
  because this budget must survive process restarts.
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

Eighteen stdlib tests cover atomic quota caps, eight competing threads, restart
persistence, idempotency, bad input, backward time, overrun freeze, and POSIX
ledger/WAL privacy, symlinks, hardlinks, relative paths, and parent-directory safety. The
module imports no providers or credentials and makes no network requests.

The planned follow-up is to integrate this as a mandatory preflight of a
sandboxed provider adapter, enforce the real provider rate limits separately,
and run a staged 4/16/32 worker concurrency test before any larger ramp. This
does **not** authorize activating the 300-worker fleet.
