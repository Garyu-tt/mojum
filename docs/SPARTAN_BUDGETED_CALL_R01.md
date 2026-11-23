# Spartan budget-gated provider call R0.1

This opt-in adapter composes the existing durable ledger in
`tools/spartan_budget.py` with one synchronous provider invocation. It closes
the gap documented in `SPARTAN_BUDGET_R01.md`: the ledger is only a safety
mechanism when trusted dispatcher code refuses to call a provider unless the
reservation is durably admitted.

## Contract

1. The trusted caller supplies a unique request ID, worker slot, maximum token
   reservation and maximum cost reservation in integer micro-USD.
2. `dispatch()` calls the canonical ledger's atomic `reserve()` first.
   Exhausted/frozen budgets and conflicting IDs raise before provider code runs.
3. A repeated identical request ID returns `dispatched=false`; the provider
   callback is never invoked a second time. Ambiguous retries require a new ID
   and a new reservation.
4. Only after a first reservation succeeds is the synchronous injected
   `invoke()` callback called once. There is no retry loop.
5. Trusted dispatcher code extracts an exact usage receipt containing native
   non-negative integer `actual_tokens` and `actual_micro_usd`. A failed
   invocation, invalid receipt or receipt-extraction exception leaves the
   entire reservation debited. It does not refund uncertain delivery.
6. The canonical ledger settles the receipt. Usage overrun or contradictory
   settlement freezes the ledger under its existing rules.

The provider response is returned to the in-memory caller only after successful
settlement. The adapter itself does not write prompts, completions, credentials,
provider exceptions or response data to the ledger.

## Example

```python
from contextlib import closing
from tools import spartan_budget
from tools.spartan_budgeted_call import dispatch

db = spartan_budget.connect("/absolute/private/0700-dir/budget.sqlite3")
try:
    spartan_budget.schema(db)
    result = dispatch(
        db,
        request_id="task-123-attempt-1",
        worker=3,
        reserve_tokens=2048,
        reserve_micro_usd=12000,
        invoke=lambda: local_provider.complete(prompt, max_tokens=1024),
        # This trusted adapter must parse the provider's documented usage
        # receipt and apply a separately pinned price schedule.
        receipt_extractor=trusted_usage_receipt,
    )
    if result.dispatched and result.settled:
        consume_proposal(result.response)
finally:
    db.close()
```

Do not estimate tokens from character counts or invent provider prices. The
caller must provide a trustworthy receipt extractor and a pinned rate schedule
(or a zero micro-USD amount for a genuinely local no-fee backend). If the
provider supplies no exact usage receipt, this adapter intentionally leaves the
reservation unsettled and debited.

## Reproduction

```sh
python -m unittest discover -s tests -p 'test_spartan_budgeted_call.py' -v
```

The tests use an in-memory SQLite database and injected callbacks; they do not
contact a model server or use provider credentials.

## Non-goals and limits

- Not yet wired into `spartan_local_fleet.py`, Cezar, Gemini or any production
  service. A trusted dispatcher must explicitly call this adapter before
  handing a request to its provider adapter.
- Not a provider-side billing hard cap, invoice reconciler, token estimator,
  provider rate-card source, credential manager, scheduler or retry policy.
- No budget reset/refund/expiry. A crash after reserve is charged at the full
  reservation; an ambiguous timeout must not be retried with the same ID.
- The SQLite ledger must remain private to trusted dispatcher code. Do not let
  model-generated or sandboxed code choose its own reservation or write to it.
