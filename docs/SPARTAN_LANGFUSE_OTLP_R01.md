# Spartan / Cezar Langfuse OTLP tracing, opt-in R0.1 (#697)

## Scope

This is a functional, strictly allowlisted standalone export adapter, **not automatic Cezar instrumentation**, a deployed dashboard, a scheduler, or independent QA evidence. Offline usage is Python stdlib only. It cannot read the Aquarium, Noosphere, GENESIS, Cezar database, source files, prompts, or model completions.

Source: tools/spartan_langfuse_trace.py
Tests: tests/test_spartan_langfuse_trace.py

Test from repository root:

~~~bash
python3 -m unittest discover -s tests -p 'test_spartan_langfuse_trace.py' -v
python3 tools/spartan_langfuse_trace.py --input /path/to/PRIVATE-safe-events.jsonl
~~~

Example synthetic input: one UTF-8 JSON object per line, **stored in a 0600 owner-only file**:

~~~json
{"schema":"mojum.spartan.telemetry/1","event_id":"evt-1","task_id":"issue-697","attempt":2,"phase":"provider","outcome":"ok","at_ms":1760000000000,"duration_ms":25,"input_tokens":13,"output_tokens":7,"cost_usd_micros":23}
{"schema":"mojum.spartan.telemetry/1","event_id":"evt-2","task_id":"issue-697","attempt":2,"phase":"qa","outcome":"reject","at_ms":1760000001234,"duration_ms":20,"input_tokens":0,"output_tokens":0,"cost_usd_micros":0,"pr_head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","ci_passed":true}
~~~

The example head SHA is a synthetic placeholder, not a real PR result. Never commit live event logs.

## Security contract

* Versioned schema: mojum.spartan.telemetry/1. The parser rejects **every unknown field** before producing OTLP.
* Required: schema, event_id, task_id, attempt, phase, outcome, at_ms, duration_ms, input_tokens, output_tokens, cost_usd_micros. Optional: pr_head (40 lowercase hex) and ci_passed (boolean, QA only).
* Phases: admission, provider, patch_test, pr_publish, qa. Outcomes: ok, error, reject, skip. PR/QA require exact SHA, QA requires CI outcome.
* Only provider events may carry bounded token/price measurements. The adapter deliberately never forwards source text, freeform tags, inputs, outputs, prompts, hostnames, user IDs, PRIVATE Noosphere events or secrets.
* Each task ID and attempt is combined and HMAC-SHA256-derived into a trace ID; event IDs are HMAC-derived into span IDs. The per-export HMAC secret must be at least 32 bytes. This is pseudonymization, **not authentication, encryption, nor independent task or CI verification**.
* Reject duplicate JSON keys and event IDs, nonfinite/bool numeric fields, unexpected keys, wrong permissions, symlink inputs, >512 events, >4096-byte lines, >1 MiB OTLP payload. Inputs are read via file descriptors; POSIX requires 0600 owner-private regular files.
* Default is network-free summary; results are explicitly suffixed/referred to as **reported**. The count ci_green_qa_rejects_reported represents claimed green CI with QA rejection, not a verified false-success metric.

## Explicit network opt-in

Review destination and input first. Use an operator-approved secret manager for credentials; do not commit them or paste production credentials into tickets. Example ephemeral CLI environment:

~~~bash
export MOJUM_TRACE_HMAC_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export LANGFUSE_PUBLIC_KEY='YOUR_PUBLIC_KEY'
export LANGFUSE_SECRET_KEY='YOUR_SECRET_KEY'
python3 tools/spartan_langfuse_trace.py --input /path/to/PRIVATE-safe-events.jsonl \
  --send --base-url https://your-trusted-langfuse.example
~~~

No request happens without --send. The destination must be an HTTPS origin with no URL credentials, extra path, query or fragment. Redirects and automatic retries are forbidden. Single request limit: 1 MiB, five-second timeout. Errors do not log endpoint details, input payload, or authentication.

The exporter builds OTLP/HTTP JSON for /api/public/otel/v1/traces and sets x-langfuse-ingestion-version: 4. This is compatible with the current Langfuse v4 ingestion protocol and **avoids the retired legacy trace endpoint**. Network delivery has only been verified with an injected fake transport in unit tests; a real Langfuse server integration test is still mandatory before production.

## Current upstream compatibility and licenses (2026-11-23)

Langfuse Cloud retired legacy trace events on /api/public/ingestion on November 16, 2026. OpenTelemetry HTTP/JSON and the v4 ingestion header are the supported route:
- https://langfuse.com/integrations/native/opentelemetry
- https://langfuse.com/docs/compatibility
- https://langfuse.com/docs/api-and-data-platform/features/public-api

Langfuse open source repository is MIT-licensed **except enterprise ee directories**:
- https://github.com/langfuse/langfuse

Hosted Langfuse avoids maintaining a larger multi-service backend but sends permitted telemetry outside the user's infrastructure. Confirm region, retention and data processing terms first. Self-hosting keeps the data within the operator-controlled stack but introduces ClickHouse, Redis, PostgreSQL/object-store operations and substantial memory/CPU overhead beyond the Mojum offline fixture. Review or disable the self-hosted instance's automatic usage telemetry and control backups/credentials before handling sensitive traces. Neither option becomes a mandatory Mojum dependency.

## Outstanding acceptance work

1. Add trusted Cezar/provider/patch/test/PR/QA collector hooks, separate from untrusted model execution; this slice deliberately does **not** instrument the dispatcher directly.
2. Join reported OTLP events to independently verified exact-head CI and QA records before treating dashboard counts as real acceptance quality.
3. Run a real disposable Langfuse v4 instance and verify rejection, replay, delivery semantics and recovery from failures.
4. Benchmark 1/2/4/8 workers, CPU/RAM/disk, batch backpressure, idempotent delivery and long offline periods; do not claim deployment throughput from synthetic tests.
5. Independent acceptance and exact-head CI are required before merge.

The adapter has no authority to reassign leases, bypass fencing, deploy to hosts or authorize PR merges. No PRIVATE journal or production machine is modified.
