# Trace envelope R01 (issue #697)

Offline, dependency-free, opt-in admission of **PUBLIC_SYNTHETIC** lifecycle metadata.
This is an additive precursor to tracing, not a Langfuse integration.

## Contract

`admit_trace(dict)` accepts exactly eight fields:
`schema="mojum.trace-envelope/1"`, `provenance="PUBLIC_SYNTHETIC"`,
`consent=true`, `trace_id` (8–64 ASCII alphanumeric, underscore or hyphen,
starting alphanumeric), `phase` (admission/patch/test/review/acceptance),
`outcome` (ok/failed/blocked/skipped), `duration_ms` (integer 0–86400000),
and `attempt` (integer 1–1000).

Rejects unknown/missing fields, subclass containers, bool-as-int, invalid enums,
identifiers that might contain URLs/emails, and non-synthetic provenance.
Errors never include submitted values. Successful results are detached, immutable
top-level scalar metadata with a canonical SHA-256 consistency checksum.

No free-form spans, prompts, source code, stack traces, credentials, IP addresses,
private Noosphere content, provider calls, network, persistence, or telemetry
export. The checksum does not authenticate a producer, verify consent, or prove
absence of sensitive information encoded into an allowed identifier.

## Test

`python -m unittest discover -s tests -p test_trace_envelope.py -v`

Requires Python 3.11+ standard library. Integration, production consent
authorization, and independent QA are separate release gates.
