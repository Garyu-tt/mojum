# QA escalation policy R0.1

## Purpose

This is a deterministic routing primitive for the #1179 QA supervisor. It
consumes an explicitly supplied metadata snapshot and returns one of three
routes:

- `LOCAL_REVIEW`: no escalation trigger was supplied.
- `ESCALATE`: enqueue independent strong-model review.
- `HUMAN_BLOCK`: stop automated judgement until authorization or revision
  freshness is resolved by an authorized human/process.

It does not call a model, poll GitHub, create work, inspect diffs, approve
reviews, authorize merges, or execute code. The caller owns scheduling and
must independently verify every factual claim.

## Input contract

The exact schema is `mojum.qa-escalation-input/1` with exactly these fields:
`schema`, `pr_number`, `head_sha`, `confidence_milli`, `risk_flags`,
and `qa_verdicts`.

- `pr_number`: positive native integer, bounded to signed 32-bit.
- `head_sha`: lowercase 40-character commit SHA.
- `confidence_milli`: native integer from 0 through 1000. It is a routing
  hint only, never proof of correctness.
- `risk_flags`: at most 32 unique allowlisted strings:
  `security_sensitive`, `authorization_ambiguous`, `stale_revision`,
  `unsafe_input`, `schema_migration`.
- `qa_verdicts`: at most 8 values from `PASS`, `FAIL`, `UNKNOWN`.

Unknown fields, flags, verdicts, duplicate flags, booleans masquerading as
integers, and malformed or oversized values are rejected.

## Routing precedence

1. `authorization_ambiguous` or `stale_revision` -> `HUMAN_BLOCK`.
2. Security-sensitive paths, unsafe-input or schema-migration findings,
   contradictory PASS/FAIL verdicts, or confidence below 600 -> `ESCALATE`.
3. Otherwise -> `LOCAL_REVIEW`.

A human block takes precedence over strong-model escalation. A model cannot
resolve authority or make a stale revision current.

## Receipt and privacy

The result is an immutable scalar/tuple dataclass with stable reason codes and
SHA-256 consistency digests over canonical JSON. The receipt contains only
the PR number, pinned head, route, reason codes and input digest. SHA-256 is a
consistency witness, not authentication or tamper-proof provenance. The caller
must protect and authenticate upstream metadata.

No filesystem, network, clock, subprocess, third-party dependency, private
Noosphere record, token, prompt, diff, host identifier or runtime integration
is used. The contract is CPU-first and deterministic.

## Run tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_qa_escalation_policy.py' -v
```
