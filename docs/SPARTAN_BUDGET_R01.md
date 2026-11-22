# Spartan model-call budget R0.1 (offline, opt-in)

This is a **standalone accounting gate**, not a deployed executor. It makes a
single coordinator's pre-dispatch reservation atomic across workers, without
model keys, network calls or invoking any provider. Pair it with the proposed
300-slot dispatcher in #477 and an independently sandboxed executor from #478.

Each API attempt needs a **new unique request ID**, a worker slot 1..300, an
upper bound on total tokens and on cost in **micro-USD (millionths of a USD)**.
The same request ID can authorize dispatch **exactly once**. If a process
crashes after reservation, the reservation remains debited, even if no usage
receipt was obtained. An ambiguous timeout must not trigger a second dispatch
under the same ID. A retry with a fresh ID requires additional budget.

Example, from repository root:

    python3 tools/spartan_budget.py --db ./spartan-budget_PRIVATE.sqlite3 init --tokens 100000 --micro-usd 1000000
    python3 tools/spartan_budget.py --db ./spartan-budget_PRIVATE.sqlite3 reserve --id task123-attempt1-model1 --worker 3 --tokens 4096 --micro-usd 25000
    python3 tools/spartan_budget.py --db ./spartan-budget_PRIVATE.sqlite3 settle --id task123-attempt1-model1 --worker 3 --actual-tokens 2940 --actual-micro-usd 11325
    python3 tools/spartan_budget.py --db ./spartan-budget_PRIVATE.sqlite3 status
    python3 -m unittest discover -s tests -p 'test_spartan_lifetime_budget.py' -v

All operations run with SQLite WAL and BEGIN IMMEDIATE transactional locking.
The cap is immutable once initialized, even if the ledger is reopened.
Reserve returns dispatch_allowed=true only on the first successful call,
false on subsequent same-ID calls. The latter **must not** invoke the model.
Conflicting reused IDs, exhausted caps and over-reservation fail closed.
A changed provider settlement receipt commits a durable frozen state before
rejecting the contradiction; it never reopens admission or overwrites the
previously recorded usage. An identical repeat remains idempotent. Settlement records actual usage but **never refunds** a
reservation: timeouts, crashes and delayed provider invoices remain safe with
respect to the *local reserved cap*.

## Strict safety boundary

- This ledger is **not** integrated into the CPU or GPU workers yet. Merely
  checking the CLI does **not** enforce budgets unless the caller refuses to
  dispatch when reservation fails or dispatch_allowed is false.
- It proves only local reservation accounting, **not real provider spend**.
  Actual bills can exceed estimated maxima, especially with retries or
  provider-side rounding. Enforce token ceilings, quota alerts and hard
  spending limits on the provider as well; reconcile actual invoices separately.
- A model worker must not control the budget ledger directly or pick its own
  quota. The trusted dispatcher reserves **before** handing a request to the
  untrusted adapter. Restrict SQLite file write access to that trusted process.
- A local model with no third-party fee may use --micro-usd 0, but token
  capacity still needs a bounded positive reservation.
- No reset, release, refund, automatic expiry, credential management, RPC or
  budget scaling is implemented. Deliberately conservative. Never mirror a
  live WAL-backed ledger using a raw file copy or assume this database is HA.
- Request IDs and counts are non-sensitive metadata. Store the PRIVATE ledger
  on the coordinator host and never put prompts or API keys into it.
