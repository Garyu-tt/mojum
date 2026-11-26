# Exact inflight audit cost totals

`tools/spartan_inflight_audit.py` accepts nonnegative decimal-string costs
with at most nine fractional places and a per-event maximum of 1,000,000 USD.
The audit sums completed successful and failed calls using integer nano-dollar
units. Its totals do not depend on the embedding application's Decimal
precision, rounding, exponent limits or traps, and do not change that context.

The existing report format is preserved: `cost_usd` is a fixed-point string
with the greatest fractional scale among completed costs, including trailing
zeros. Integer-only costs and an empty log emit integer strings. For example,
`0.123456789 + 0.000000001` emits `0.123456790`; `1.20 + 2.3000` emits `3.5000`.

The event count remains bounded by the existing admission limit. Costs from
unfinished calls are not invented or charged. Existing input grammar,
per-event bounds, request/response identity checks and concurrency measurements
are unchanged. The result remains collector telemetry, not proof that a
provider billed or executed the reported operations.
