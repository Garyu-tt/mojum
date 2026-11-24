# Independent review evidence R0.1

This package validates a caller-supplied, bounded snapshot of GitHub PR review
events. It is an offline consistency check, not an authenticated GitHub
attestation, policy engine, or merge authorization.

## Contract

Schema: `mojum.independent-review-evidence/1`.

A snapshot must declare `PUBLIC_SYNTHETIC` provenance and explicit consent.
It pins PR number, author, last pusher, current head SHA, base SHA, open state,
and non-draft state. Review events contain an ID, reviewer login, state,
commit SHA, and positive submission sequence/timestamp.

The latest event per reviewer controls that reviewer's state. A qualifying
approval must be `APPROVED`, name the exact current PR head, and come from a
login distinct from both PR author and last pusher. A later dismissal,
comment, or changes-requested event invalidates that reviewer's earlier
approval. Empty, missing, duplicate, stale, malformed, unknown, or oversized
evidence fails closed.

## Use

Run the focused suite from the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_independent_review_evidence -v
```

The function `validate_review_evidence(snapshot)` returns a frozen receipt
with a deterministic SHA-256 consistency digest. The digest is not a
signature and does not prove the snapshot came from GitHub.

## Explicit non-goals

- No GitHub API calls, polling, credentials, webhooks, or key material.
- No review submission, branch-protection mutation, merge, or merge permission.
- No verification of reviewer authorization beyond identity inequality in
  the supplied snapshot; callers must obtain the snapshot from a trusted
  source and separately enforce repository permissions.
- No inference from comments, check-runs, schedules, or model-generated text.
- No access to PRIVATE Noosphere records, journals, devices, or host services.
