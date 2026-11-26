# Spartan / Langfuse sanitized observability R0.1 (issue #697)

This is a **working opt-in Python SDK bridge**, not a deployed Langfuse service,
a Cezar feature flag, a replacement scheduler, or proof of independent PR
acceptance. Source: `tools/spartan_langfuse.py`. It requires no third-party
packages in Mojum's normal Python 3.11+ CPU-first install.

## Data and authority boundary

- Disabled by default. `publish(..., enabled=False)` does not touch its event,
  caller-supplied client or secret. Importing this module performs **no network
  access, credential lookup, SDK import, DB open, background thread or I/O**.
- An explicit caller supplies a Langfuse SDK client, a **secret 32..128 byte HMAC
  key** and a typed `SafeEvent`. The adapter does not create or configure a
  Langfuse instance or export raw source, prompts, model outputs, exception
  strings, organism identifiers, PRIVATE Noosphere events, tokens or host paths.
- Only stage/status enums, numeric timing/usage/cost, measured CI/QA booleans,
  attempt/fencing epoch/sequence, and an **exact full lowercase PR HEAD SHA**
  may leave the process. The opaque lease ID is **never transmitted**: keyed
  HMAC-SHA256 derives a 128-bit trace ID and an event ID from the lease and
  typed values. Use a distinct key per environment, handle it outside the
  repository and rotate deliberately (rotation changes correlation).
- `ci_complete` and `qa_review` require measured booleans; QA must carry
  both CI and independently observed QA outcomes. Their status cannot contradict
  the corresponding measured result: only a true outcome can carry `success`;
  false outcomes may be `failure`, `blocked` or `deferred`.
  A CI pass with a QA failure
  increments `ci_false_success` in the offline summary. Unknown is never
  silently treated as a passing check.
- Every PR/CI/QA record carries the exact PR commit SHA. Callers must verify
  the SHA against live GitHub before recording an acceptance fact. The bridge
  itself **does not** query GitHub, sign approvals, schedule workers or merge.
- SDK transmission exceptions yield `False` without leaking exception text,
  leaving worker execution and independent QA untouched. They are **not**
  proof of successful delivery. Langfuse itself may buffer and retry; this
  bridge does not persist an outbox or guarantee once-only network ingestion.
- The local `summarize` function rejects conflicting replay sequence IDs,
  deduplicates identical events, and processes **at most 10,000** input
  events per call (including duplicates). It is an ephemeral dashboard counter, not canonical SQLite
  history, not a durable audit log, and not a source of acceptance authority.

## Optional upstream compatibility and licensing

- Candidate: [Langfuse Python SDK 5.2.0](https://pypi.org/project/langfuse/5.2.0/)
  (published 2026-11-05), **MIT license**, documented
  [5.x SDK API](https://langfuse.com/docs/observability/sdk/overview).
- Python SDK 5 requires Langfuse Cloud or self-hosted Langfuse **v4.59.0+**;
  **do not** point it at a self-hosted v3 server. Confirm compatibility before
  any deployment. The Langfuse application repository is
  [MIT except enterprise subdirectories](https://github.com/langfuse/langfuse/blob/main/LICENSE),
  which have separate commercial terms. License review is preliminary:
  dependency inventory, data-retention terms and legal review remain required
  before changing `optional_integrations.json` from `planned`.
- No mandatory install. In an **isolated opt-in environment only**:
  `python -m pip install langfuse==5.2.0`. Credentials, data location,
  exported spans, storage/retention and TLS/egress must be approved
  out-of-band. A shared cloud account is **not** appropriate for PRIVATE
  organism memory. Disable other auto-instrumentation that can collect prompts.
- SDK sample contract (for a permitted synthetic-only test):

```python
import os
from langfuse import get_client
from tools.spartan_langfuse import SafeEvent, publish, summarize

event = SafeEvent(
    stage="qa_review", lease_id="synthetic-lease-1", attempt=1,
    fencing_epoch=1, sequence=3, status="failure",
    pr_head_sha="a" * 40, ci_passed=True, qa_passed=False, latency_ms=42,
)
# Explicit authorization is required. Never store this private key in Git.
hmac_key = bytes.fromhex(os.environ["MOJUM_TRACE_HMAC_HEX"])
client = get_client()  # configured by Langfuse's own approved environment
sent_to_sdk = publish(event, enabled=True, client=client, secret=hmac_key)
# sent_to_sdk only means the SDK context manager returned without error.
# If needed, flush at a controlled shutdown boundary; not in worker critical path.
print(summarize([event]))  # local only: ci_false_success = 1
```

## Resource and failure gates

- Python package baseline: **zero new dependencies**, zero egress until
  explicit client injection and `enabled=True`.
- Per-event caps: 120-character lease ID, exact 40/64 hex SHA, max
  86,400,000 ms latency, 100,000,000 tokens per direction,
  100,000,000,000 micro-USD, 10,000 attempts, 2^63-1 fence.
  All numeric bounds reject bools, negatives and overflow.
- Offline counters are O(n) with a 10,000-input-event cap; HMAC and JSON hashing
  are bounded per record. These are **design limits**, not measured CPU/RAM
  utilization. Production benchmarks and exporter queue budgets are pending.
- Regression: `python -m unittest discover -s tests -p 'test_spartan_langfuse.py' -v`.
  Covers default no-op, exact allowlist, different lease epochs, deterministic
  trace/event IDs, forbidden fields, malformed SHA, failed SDK, replay conflict,
  QA false-success and bounded summarization.
- A live SDK/server integration and dashboard deployment are **not run** by
  these offline tests. A follow-up acceptance exercise must verify actual
  storage/export with disposable synthetic data and independent review before
  setting any production flag.

## Integration points (explicitly not wired on this branch)

Wrap real Cezar/Spartan lifecycle events at the admission, patch/test,
PR opened, CI and independently validated QA boundaries. Only the authoritative
lease/attempt/fence owner should construct each `SafeEvent`. Do not infer
`ci_passed` from a queued run, and do not let a Langfuse signal influence
Cezar task leases or GitHub protected-main approval.
