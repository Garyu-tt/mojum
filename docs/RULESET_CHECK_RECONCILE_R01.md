# Ruleset / Check Runs reconciliation R0.1

Issue: #712. Module: `mojum.ruleset_check_reconcile`.

## Purpose

Compare the active GitHub branch ruleset's required-check descriptors with a
complete Check Runs API snapshot for one exact commit SHA. A required check is
identified by both its `context` and `integration_id` (GitHub App ID). A run
counts only when its `name`, `app.id`, and `head_sha` all match that pair and
the explicitly supplied SHA.

The result is one of:

- `PASS`: every configured check has exactly one completed success/neutral
  Check Run from the configured integration on the exact SHA.
- `HOLD`: ruleset evidence is inactive/mis-targeted/non-strict, check-run
  pagination is incomplete, or any check is missing, pending, unknown, or
  ambiguous.
- `REJECT`: the evidence is complete and at least one required check has a
  failing, cancelled, timed-out, action-required, stale, or skipped conclusion.

Legacy combined commit statuses are not accepted as Check Runs. This directly
addresses the distinction exposed by the #712 acceptance investigation.

## API

```python
from mojum.ruleset_check_reconcile import reconcile_ruleset_checks

result = reconcile_ruleset_checks(ruleset_json, check_runs_json, expected_sha)
print(result.verdict, result.missing, result.receipt_sha256)
```

Pass the JSON objects returned by:

- `GET /repos/{owner}/{repo}/rulesets/{ruleset_id}`
- `GET /repos/{owner}/{repo}/commits/{ref}/check-runs?per_page=100`

The caller must fetch every page and supply the full `total_count`; partial
pagination produces HOLD. The expected SHA must be the exact head or tested
merge commit being evaluated. Run evidence from another SHA never satisfies
the requirement. Duplicate matching runs are ambiguous until the caller
reduces them to one authoritative latest attempt.

## Trust and authority boundaries

- Pure standard library, deterministic, bounded and read-only.
- No GitHub/network calls, clock reads, credential handling, approval
  submission, merge, branch-protection changes, bypass, or live deployment.
- Caller-supplied snapshots are not authenticated by this function. The
  SHA-256 receipt is consistency metadata, not a signature or proof of source.
- PASS means only that the supplied snapshots reconcile; it is not merge
  authorization and does not replace independent review, current HEAD/BASE
  checks, unresolved-thread checks, or GitHub's own ruleset enforcement.
- Never infer successful Check Runs from an empty legacy combined-status API.
  If a merge commit has zero Check Runs, the correct result is HOLD.

## Limits

At most 32 required contexts and 256 check-run records per supplied snapshot.
Context names are bounded to 200 characters. Inputs must be exact native
Python dict/list/string/integer values at the documented boundaries; booleans
are not integers. Malformed shapes raise `ValueError`.
