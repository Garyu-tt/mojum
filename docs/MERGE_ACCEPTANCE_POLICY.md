# Merge acceptance policy (issue #415)

Automated and agent merges to `main` must never rely on a best-effort poll.

1. Resolve the PR's current head SHA immediately before merging
   (`gh pr view N --json headRefOid`).
2. Inspect the checks **for the pinned commit**, not just the mutable
   pull-request UI. With `REPO=owner/name` and `SHA=<headRefOid>`, query
   `gh api --paginate "repos/$REPO/commits/$SHA/check-runs"` and
   `gh api "repos/$REPO/commits/$SHA/status"` (classic statuses); verify
   the returned `head_sha` belongs to `SHA`. For Actions workflows whose
   check results are associated with a synthetic PR merge ref, also inspect
   the workflow-run metadata and require its **source** `head_sha` to match
   `SHA`; verify the run's tested base is still relevant. `gh pr checks N`
   is supplementary, never the only evidence of SHA identity.
3. Confirm that the required Aquarium regression, Motor Gate and Mesh
   contract contexts are **present and successful** for the exact head.
   Zero checks, missing expected contexts, pending, queued, in-progress,
   cancelled, timed-out or failed are blockers, not vacuous success. A skipped
   required check cannot be waived in PR text. A skipped *non-required* check
   needs an explicit documented waiver.
4. Re-read the PR head and base immediately before merging. Require the head
   to still equal `SHA`, the base to still be `main`, and the comparison to
   remain merge-compatible; if `main` advanced, re-evaluate integration and
   applicable checks. Merge with `gh pr merge N --squash --match-head-commit
   <sha>` so a concurrent source push is rejected.
5. Never bypass rulesets, force push, or open and merge your own fix in the
   same cycle before its checks finish. Leave it for a later review pass.
6. Repository-level enforcement (a ruleset requiring Aquarium regression,
   Motor Gate and Mesh contract checks) is an owner setting; this note is the
   interim agent-side policy only and does not replace it.
