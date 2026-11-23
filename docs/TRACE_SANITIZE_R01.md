# Privacy-safe trace attributes R0.1

## Scope

`mojum.trace_sanitize` is an opt-in, offline, standard-library-only boundary
for a small set of operational trace attributes. It does not integrate a
telemetry SDK, open a network connection, read a journal, or inspect arbitrary
application objects.

## Wire contract

Schema: `mojum.trace-attributes/1`.

Accepted string fields:
- `trace_id`, `lease_id`: 32–64 lowercase hexadecimal characters.
- `head_sha`: 40- or 64-character lowercase hexadecimal commit identifier.
- `event`, `component`, `operation`, `reason_code`: bounded lowercase
  machine-readable slugs.
- `outcome`: success, failure, skipped, deferred, rejected.
- `status`: ok, error, pending, blocked, complete, incomplete.

Accepted integer fields (exact built-in int, 0 through 2^63-1):
`fencing_epoch`, `pr_number`, `duration_ms`, `latency_ms`,
`input_tokens`, `output_tokens`, `cost_microusd`. A PR number must be
positive. bool is not an integer for this contract.

At most 24 attributes are accepted. Output is a detached dict sorted by key.
A canonical SHA-256 digest can be produced for validated attributes.

## Fail-closed behavior

Unknown keys, non-exact dicts, non-string keys, nested values, arbitrary
objects, floats, free-form text, malformed identifiers, negative/oversized
counters and excessive attribute counts raise `TraceSanitizationError`.
The sanitizer rejects rather than silently redacting because a caller must
know that its proposed trace schema is incompatible.

## Privacy boundary and non-claims

The accepted vocabulary intentionally excludes prompt text, source snippets,
PRIVATE Noosphere records, user/organism content, credentials, tokens,
environment variables, filesystem paths, hostnames, IP addresses, device
identifiers and arbitrary labels. Callers must still ensure that values are
public/synthetic operational metadata before calling this function.

This is not a complete information-flow security system, a secret scanner,
a Langfuse integration, authentication, encryption, or permission to export
private data. A valid digest proves only deterministic encoding of the
accepted metadata. No existing journal, policy identity, scheduler, or runtime
is changed.
