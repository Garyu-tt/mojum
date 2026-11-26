# Offline reviewer identity gate R0.1

Issue: #1109 (parent #712)

## Purpose

This opt-in helper is a narrow admission check for GitHub review snapshots.
It complements, but does not modify, the exact-head CI rollup in
`mojum.merge_gate`. The helper accepts a caller-supplied review list and
returns `ready` only if at least one reviewer's **latest** review is
`APPROVED`, targets the exact pinned PR head SHA, and the reviewer GitHub
numeric user ID differs from both the PR author and the last pusher.

The helper never contacts GitHub, submits a review, changes permissions,
merges a PR, or modifies branch protection.

## Input contract

Call `evaluate_review_snapshot(snapshot)` with exactly these fields:

- `schema`: `mojum.reviewer-identity.v1`
- `head_sha`: full 40- or 64-character Git object ID
- `author_id`, `last_pusher_id`: positive native integer GitHub user IDs
- `reviews_complete`: native boolean; set true only after every page of the
  GitHub PR review list has been fetched
- `reviews`: at most 1,000 review records, each containing exactly
  `review_id`, `user_id`, `login`, `state`, `commit_sha`, `submitted_at`

Review states are restricted to `APPROVED`, `CHANGES_REQUESTED`,
`COMMENTED`, and `DISMISSED`. Latest event is selected per numeric user
ID by UTC-aware submitted timestamp, then review ID as a deterministic
tie-breaker. A later comment, dismissal, or change request supersedes an
earlier approval. An approval for an older commit does not qualify.

## Verdicts

| Verdict | Exit | Meaning |
| --- | ---: | --- |
| `ready` | 0 | At least one distinct reviewer has a latest exact-head approval |
| `defer` | 2 | Review pages are incomplete or no eligible approval exists yet |
| exception | n/a | Malformed, oversized, or ambiguous input; caller must fail closed |

The report contains only verdict, reason code, object ID, counts and qualifying
numeric reviewer IDs. Review bodies, names, profile fields and other metadata
are neither accepted nor returned.

## Example

```python
from mojum.reviewer_identity_gate import evaluate_review_snapshot

result = evaluate_review_snapshot({
    "schema": "mojum.reviewer-identity.v1",
    "head_sha": "a" * 40,
    "author_id": 10,
    "last_pusher_id": 11,
    "reviews_complete": True,  # caller asserts all pages were fetched
    "reviews": [{
        "review_id": 9001,
        "user_id": 30,
        "login": "independent-reviewer",
        "state": "APPROVED",
        "commit_sha": "a" * 40,
        "submitted_at": "2026-11-23T18:00:00Z",
    }],
})
assert result.verdict == "ready"
```

Run isolated tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_reviewer_identity_gate.py' -v
```

## Security and limitations

- A numeric GitHub user ID distinguishes accounts, not humans or beneficial
  ownership. The helper cannot prove that two accounts are controlled by
  different people or that an account is an authorized reviewer.
- `reviews_complete` is a caller assertion. This module cannot attest that
  GitHub pagination was actually exhausted or that the supplied PR metadata
  came from an authenticated live API response.
- The last-pusher ID must be read from authoritative PR/commit metadata by
  the caller and pinned to the same HEAD. The module cannot infer it from
  review events.
- This offline preflight is not a substitute for repository rulesets,
  required reviewers/CODEOWNERS, stale-review dismissal, current base/head
  revalidation, required CI checks, or an independent review service.
- Never treat `ready` alone as merge authorization. Pair it with
  `mojum.merge_gate`, live GitHub evidence and server-enforced protections.

No review body, private Noosphere record, token, network request, device
control, SQLite write, merge action or production deployment is in scope.
