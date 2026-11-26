# Spartan HTTP retry decision R0.1 (Issue #953, parent #478)

Pure, **offline** Python 3.11+ helper at `mojum.spartan_retry_policy`. It is a
candidate decision contract, **not an attached/executing provider adapter**.
No extra packages, network, global clock, sleep, credentials, SQLite, or
background worker are involved. Existing coordinator/fleet code is untouched.

## Usage

```python
from datetime import datetime, timezone, timedelta
from mojum.spartan_retry_policy import RetryPolicy, plan_retry

now = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)
decision = plan_retry(
    status_code=429, failed_attempt=1, now_utc=now,
    replay_safe=True, retry_after="2",
    deadline_utc=now + timedelta(seconds=10),
    remaining_wait_ms=10_000,
    policy=RetryPolicy(max_attempts=4, initial_delay_ms=250),
)
assert decision.should_retry and decision.delay_ms == 2_000
# A separate executor MAY schedule a retry only after rechecking live quotas,
# idempotency, task lease/fencing, deadline, and actual wall-clock budget.
```

API uses `failed_attempt=1` for the first failed call, and `max_attempts`
counts the original attempt. Only HTTP 408, 429, 500, 502, 503, 504 are
retryable candidates. `replay_safe` must be explicitly `True`; it is not proof
of idempotency. Implementers must use stable server-side idempotency keys or
otherwise show a repeated request cannot duplicate cost/work. The returned
`RetryDecision` is frozen, marked `mojum.spartan.http-retry-decision/1`, and
contains `should_retry`, `delay_ms` and a finite reason string.

* Backoff: `min(max_backoff_ms, initial_delay_ms * 2**(failed_attempt-1))`.
  No random jitter or process-local entropy; deterministic fixtures and explicit
  server wait floors are intentional. A later scheduler may add **non-negative**
  bounded jitter only if it revalidates deadlines and never advances the retry.
* Header: accepts only ASCII `delta-seconds` (up to 10 digits) or *canonical*
  RFC IMF-fixdate `Mon, 23 Nov 2026 16:00:02 GMT`, with correct weekday.
  Malformed/oversized headers are **refused**, not ignored. Expired dates add
  zero server wait, but never bypass local backoff. Server wait beyond
  `max_retry_after_ms` is refused, not prematurely clipped.
* Bounds: max 32 attempts, nonzero integer backoff 1..24h, maximum header wait
  0..24h; 24h maximum caller wait budget. Deny retry if the delay exceeds
  remaining explicit budget, UTC deadline, server limit or representable clock.
  All datetime inputs must be native timezone-aware UTC datetimes.

The function never consumes retry budget or counts a USD/token quota; the
caller must atomically reserve these resources and fence stale workers.
If executor timing is uncertain, re-evaluate the deadline with a fresh **explicit**
clock before performing any retry. No retry is proof of safety without
provider-specific quotas, `Retry-After` extraction/duplicate-header handling,
request replay protections, audited cost limits and failure isolation.

## Offline QA

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_spartan_retry_policy.py' -v
python3 -m compileall -q src/mojum/spartan_retry_policy.py
```

Coverage: exponential cap, strict status/idempotency admission, numeric/date
header parsing, malformed/hostile data, 24h/attempt ceilings, deadlines,
wait budget, far-future refusal, UTC/native type checks, clock overflow,
deterministic output and no sleep/network interaction. Exact-head CI plus
independent acceptance are required before merge. No production rollout.
