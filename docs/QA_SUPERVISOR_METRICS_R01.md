# QA Supervisor Metrics Reducer R01

## Purpose

A pure-stdlib reducer for bounded caller-supplied QA supervisor snapshots. It is intended for dashboards and diagnostics after a trusted caller has collected data. It does not poll GitHub, read a queue, schedule work, persist records, invoke a model, approve a review, or authorize a merge.

## Input contract

`summarize_snapshot(mapping)` accepts only exact built-in containers and scalar types:
- top-level and task records must be built-in `dict` objects (subclasses rejected)
- `observed_at`: non-negative built-in integer epoch seconds (booleans and integer subclasses rejected)
- `tasks`: built-in `list` or `tuple` with at most 10,000 task dictionaries
- each task has a unique bounded built-in string `id`, a known built-in string state, and optional `age_seconds`
- optional `blocked_reason` only for blocked/waiting/escalated states, from the fixed allowlist
- optional `lease_remaining_seconds` only for active states
- optional `duration_samples_ms`: built-in `list` or `tuple` with at most 10,000 finite non-negative built-in integer/float samples

Container and scalar subclasses are rejected so caller-defined methods cannot run during validation. Unknown fields, duplicate IDs, unknown states/reasons, invalid native types, non-finite values, and out-of-range values fail closed.

## Output

The detached aggregate includes counts by state, age bucket, blocked reason, lease health, sample count and nearest-rank p50/p95 duration. It contains no task IDs or arbitrary source strings. A SHA-256 receipt covers the canonical JSON representation of the aggregate before the receipt field is added. The digest is an integrity aid, not an authenticity signature.

## Privacy and limits

The caller must supply only data it is authorized to aggregate. The reducer deliberately discards task identifiers and rejects arbitrary labels/URLs/tokens instead of attempting to redact them. Do not feed PRIVATE Noosphere content, PR bodies, credentials, host identifiers, or raw logs. The reducer has no network, filesystem, subprocess, or hardware access.

## Validation

Run with Python 3.11+:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p "test_qa_supervisor_metrics.py" -v
```

This is an opt-in diagnostic primitive. It is not evidence of CI success, QA approval, service deployment, or merge permission.
