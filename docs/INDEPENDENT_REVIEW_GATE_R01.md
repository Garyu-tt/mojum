# Independent review evidence gate R0.1

Issue: #712. Module: `mojum.independent_review_gate`.

## Purpose

A bounded, deterministic, standard-library-only policy core that evaluates a caller-supplied snapshot of PR review evidence. It returns `REVIEW_READY` only when the reviewer differs from both the PR author and last pusher, the approval is pinned to the exact current HEAD and BASE, the target is `main`, the PR is not a draft, all caller-declared required jobs have completed successfully on the current HEAD, and there are no unresolved review threads.

Every missing, stale, pending, failed, cancelled, skipped, or otherwise non-success required job produces `HOLD`. Duplicate job names are rejected because callers must reduce attempts to the latest attempt before evaluation. Malformed or oversized records raise `ValueError`.

## API

```python
from mojum.independent_review_gate import evaluate_review_evidence

result = evaluate_review_evidence(evidence)
print(result.decision, result.reasons, result.receipt_sha256)
```

Input has an exact schema: PR number, target/draft state, author/last-pusher/reviewer logins, review state and reviewed/current HEAD+BASE SHAs, required job names, latest job snapshots and unresolved-thread count. SHA values must be lowercase 40-hex strings. Container and primitive types are exact-native; booleans are not accepted as integers.

## Trust and authority limits

- This is an **evidence policy evaluator**, not an independent reviewer identity, GitHub client, signature verifier, review submission API, or merge service.
- The caller must fetch authenticated live GitHub data, verify the reviewer principal is an operator-authorized distinct account/App, verify review freshness and all required contexts from GitHub, and re-read HEAD/BASE immediately before any separately authorized merge.
- Caller-supplied fields can be fabricated. The SHA-256 receipt provides deterministic consistency, not authenticity or tamper resistance.
- The function never approves, requests changes, merges, bypasses rulesets, accesses credentials, reads private Noosphere data, or performs network/I/O.
- A `REVIEW_READY` result is only a recommendation to proceed to a separate human/authorized review step. It does not satisfy GitHub branch protection.
- No reviewer identity can be provisioned by this patch. The external operator-authorized distinct principal remains a hard blocker for shipping.

## Composition with review-history evidence (#1334)

Within the offline #712 layer, #1335 owns the aggregate recommendation policy.
[PR #1334](https://github.com/Garyu-tt/mojum/pull/1334) remains an unmerged Draft
proposing an optional review-history reducer and approval-candidate selector.
There is no automatic adapter or import between them. This boundary does not
replace the existing [repository merge gate](MERGE_GATE_R01.md).

Do not map #1334's `receipt.base_sha` to #1335's `reviewed_base_sha`: the former
copies the current PR snapshot and does not establish which base was reviewed.
A future adapter must obtain separate, trustworthy evidence of the reviewed
base. If that evidence is unavailable, stop with `HOLD`; do not infer it from
current `main` or supply a default.

Selecting an approval candidate does not rule out another reviewer's
`CHANGES_REQUESTED`. Authenticated data, an open PR against current `main`,
successful completion of every required CI check, and absence of unresolved
blockers remain separate verification requirements.

#1334 admits only `PUBLIC_SYNTHETIC` inputs. Live GitHub evidence must not be
relabelled as synthetic to pass admission. A future adapter with an explicit
trust contract and end-to-end acceptance of #712 remain separate tasks.

## Resource bounds

At most 32 required job identities and 128 supplied job snapshots; names and logins are bounded; no unbounded recursion, file access, network, clock reads or third-party packages.
