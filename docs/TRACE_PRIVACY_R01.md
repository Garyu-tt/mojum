# Trace privacy boundary R0.1

Issue: [#697](https://github.com/Garyu-tt/mojum/issues/697)

## Purpose

`mojum.trace_privacy` defines a dependency-free, offline admission boundary
for operational trace metadata. It is intentionally **not** a general-purpose
redactor. Arbitrary strings and nested objects cannot be made safe reliably by
guessing which substrings are secrets, so this contract uses a strict
allowlist and rejects every unrecognized field.

## Wire contract

Schema: `mojum.trace-envelope.v1`

Required scalar fields:

- `trace_id`: 32–64 lowercase hexadecimal characters
- `lease_id`: 1–128 safe ASCII identifier characters
- `fencing_token`: native integer in `[1, 2^63-1]` (booleans are rejected)
- `pr_number`: native integer in `[1, 2^31-1]`
- `head_sha`: full 40- or 64-character lowercase Git object ID
- `event_type`: one of the fixed operational lifecycle event names
- `timestamp_ms`: native integer in `[0, 2^63-1]`

Optional scalar metrics: `latency_ms`, `input_tokens`, `output_tokens`
(native integers in `[0, 2^63-1]`), and `cost_usd` (finite non-negative number).
Null optional values are omitted from the exported copy.

The validator accepts only a plain Python dictionary with at most twelve
fields. Unknown keys, nested values, malformed identifiers, booleans in
integer positions, NaN/infinity, and unsupported event names fail closed.
Error messages do not echo rejected field names or values.

## Privacy boundary

The schema deliberately has no fields for prompts, source code, diffs, model
outputs, PRIVATE Noosphere events, journal snapshots, environment variables,
credentials, hostnames, filesystem paths, IP addresses, or arbitrary labels.
Those values must never be passed to this module. Unknown fields are rejected,
not silently dropped, because silent dropping could conceal an upstream
contract mismatch.

`TraceEnvelope` is frozen and scalar-only. `as_dict()` creates a fresh
JSON-compatible dictionary, so caller mutation cannot modify the validated
record. `OfflineTraceSink` validates an event and returns `False`; it does
not buffer, persist, or transmit anything.

## Explicit limitations

- No Langfuse SDK, network transport, remote dashboard, or runtime integration.
- No cryptographic identity/authentication: trace IDs are format-checked only.
- No claim that upstream callers have already removed sensitive data; callers
  must construct a metadata-only dictionary.
- No redaction of arbitrary prompt/source/journal content.
- No production deployment or merge authorization.

## Test

Run with CPython 3.11+:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_trace_privacy.py' -v
```

The focused tests cover allowlist admission, identifier and numeric strictness,
hostile nested fields, non-finite costs, immutable-by-copy behavior, and the
offline no-op sink. Full repository CI and independent QA remain required.
