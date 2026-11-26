# Langfuse OTLP R0.1: opt-in Spartan / QA observability

Relates #697, #478 and #415. Implements an explicit, CPU-first OTLP/HTTP JSON sender for **sanitized task lifecycle events**. It does not install Langfuse, auto-instrument any provider, read Noosphere, read prompts or source files, manage Cezar leases, approve PRs, or enable production telemetry.

## Upstream and compatibility review (2026-11-23)

- Upstream: https://github.com/langfuse/langfuse
- Open-source core: MIT; enterprise ee/ modules and any third-party dependencies have separate licensing. Review deployment and retention policy before enabling external observability.
- Pinned wire format: OTLP/HTTP JSON trace export to /api/public/otel/v1/traces with x-langfuse-ingestion-version: 4. Supports Langfuse Cloud v4 and self-hosted v3.22+ / v4 according to upstream docs.
- Legacy /api/public/ingestion for trace/observation records stopped working on Langfuse Cloud on **2026-11-16**. Never use it for tracing.
- Sources: https://langfuse.com/docs/observability/get-started ; https://langfuse.com/docs/compatibility ; https://langfuse.com/faq/all/opentelemetry-logs-metrics-span-links
- The adapter intentionally uses Python 3.11+ stdlib, no SDK dependency. A live Langfuse deployment has **not** been tested; local mock HTTP transport has.

## Privacy boundary and provenance

Input schema: mojum.langfuse.quality-event/1. The adapter reconstructs an allowlist instead of recursively sanitizing arbitrary JSON. It accepts only canonical random UUIDv4 event_id and lease_id; positive integer attempt fencing; bounded timestamp_ms; fixed phases (provider_admission, provider_result, patch, ci, qa) and enumerated outcomes; optional PR number and lowercase 40/64-digit exact PR HEAD (mandatory for patch/CI/QA); fixed model class (cpu_local, gpu_local, remote), reported integer usage/cost and bounded duration.

No freeform fields, prompts, source code, exception strings, filesystem paths, model names, private events, API keys or unknown JSON keys are allowed. The raw lease and event UUIDs are never transmitted: a stable SHA-256-derived trace ID groups lease+attempt; event-specific SHA-derived span ID separates attempts. These identifiers are pseudonymous, not encrypted. Unknown usage is omitted, not reported as zero. No private SQLite journal changes.

Only authorized Cezar/Spartan provider and independent QA services should construct sanitized inputs, after measuring the actual request and checking data ownership. A CI pass by itself must never authorize merge; independent exact-head reviewer and GitHub checks remain authoritative. The local summary tracks cases where a single exact PR HEAD passes CI but QA rejects/blocks it.

## Example

Write a synthetic event to an owner-controlled local file:

    {"schema":"mojum.langfuse.quality-event/1","event_id":"ad68a14b-ea2a-49f5-9f87-e4f6063f44db","lease_id":"a0b9a623-46eb-4cbf-8a76-e65ab2e55ab0","attempt":2,"phase":"ci","outcome":"passed","timestamp_ms":1800000000000,"pr_number":42,"pr_head":"cccccccccccccccccccccccccccccccccccccccc"}

Preview OTLP JSON locally (NO network):

    PYTHONPATH=src python -m mojum.langfuse_otel < safe-event.json

Send **only after explicitly authorizing** the target project, network telemetry and retention policy:

    export LANGFUSE_PUBLIC_KEY='pk-...'
    export LANGFUSE_SECRET_KEY='sk-...'
    PYTHONPATH=src python -m mojum.langfuse_otel --send --host https://your-authorized-langfuse.example < safe-event.json

For local aggregate metrics, pass a JSON array of safe events:

    PYTHONPATH=src python -m mojum.langfuse_otel --summary < safe-events.json

The adapter requires HTTPS except numeric loopback HTTP, blocks proxy and redirects, uses 3-second bounded timeout and makes at most one attempt (no implicit replay/duplicate). Credentials come from environment only in send mode and are not included in exception text.

## Tests and budgets

    PYTHONPATH=src python -m unittest tests.test_langfuse_otel -v

Nine deterministic unit tests include a real loopback HTTP server and check OTLP envelope, authentication/version header, privacy rejects, SHA/fencing, provider metrics, exact-head QA inconsistency, no-op default, host hardening and partial acknowledgments. Tests run under CPU/stdlib with no server. Local run: 9/9 PASS (~0.05 s on fixture; **not** a production performance benchmark).

Bounded CLI: max 2 KiB/event JSON; one span per invocation; bounded duration; max 100 KiB or 10,000 events for summary; no persistent queue, local logs, retries or auto-SDK instrumentation. Remote Langfuse still receives PR HEAD, event timestamp and metrics when supplied, so deployment approval and data retention remain necessary.

## Follow-ups not claimed as completed

Wire to an independently reviewed trusted provider boundary and Cezar QA emission; obtain a live synthetic Langfuse v4 compatibility test; configure owner-approved retention, resource limits and dashboard visualization; independently accept exact-head CI before merge. Do not auto-install or activate in existing Aquarium fixtures.
