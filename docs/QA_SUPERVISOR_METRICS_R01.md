# QA Supervisor Metrics Reducer R01

## Purpose

A pure-stdlib reducer for bounded caller-supplied QA supervisor snapshots. It is intended for dashboards and diagnostics after a trusted caller has collected data. It does not poll GitHub, read a queue, schedule work, persist records, invoke a model, approve a review, or authorize a merge.

## Input contract

`summarize_snapshot(mapping)` accepts only:
- `observed_at`: non-negative integer epoch seconds (booleans rejected)
- `tasks`: at most 10,000 task mappings, each with a unique bounded `id`, a known state, and optional `age_seconds`
- optional `blocked_reason` only for blocked/waiting/escalated states, from the fixed allowlist
- optional `lease_remaining_seconds` only for active states
- optional `duration_samples_ms`: at most 10,000 finite non-negative numeric samples

Unknown fields, duplicate IDs, unknown states/reasons, invalid native types, non-finite values, and out-of-range values fail closed.

## Output

The detached aggregate includes counts by state, age bucket, blocked reason, lease health, sample count and nearest-rank p50/p95 duration. It contains no task IDs or arbitrary source strings. A SHA-256 receipt covers the canonical JSON representation of the aggregate before the receipt field is added. The digest is an integrity aid, not an authenticity signature.

## Privacy and limits

The caller must supply only data it is authorized to aggregate. The reducer deliberately discards task identifiers and rejects arbitrary labels/URLs/tokens instead of attempting to redact them. Do not feed PRIVATE Noosphere content, PR bodies, credentials, host identifiers, or raw logs. The reducer has no network, filesystem, subprocess, or hardware access.

## Validation

Run with Python 3.11+:

```sh
PYTHONPATH=src python -m unittest tests/test_qa_supervisor_metrics.py
```

This is an opt-in diagnostic primitive. It is not evidence of CI success, QA approval, service deployment, or merge permission.
