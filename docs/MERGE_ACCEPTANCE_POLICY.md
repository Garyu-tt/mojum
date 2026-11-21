# Merge acceptance policy (issue #415)

Automated and agent merges to `main` must never rely on a best-effort poll.

1. Resolve the PR's current head SHA immediately before merging
   (`gh pr view N --json headRefOid`).
2. List checks on that exact SHA (`gh pr checks N`) and confirm that the
   required Aquarium regression, Motor Gate and Mesh contract contexts are
   **present** on that SHA. Zero checks or a missing expected context are
   blockers, not vacuous success. Merge only when all applicable checks
   succeed. Pending, queued, in-progress, cancelled, timed-out or failed means
   do not merge; a skipped non-required check needs an explicit PR waiver.
3. Recheck the PR still targets `main` and remains base-compatible. Merge
   with `gh pr merge N --squash --match-head-commit <sha>` so a push
   after the check passes is rejected by GitHub.
4. Never bypass rulesets, force push, or open and merge your own fix in the
   same cycle before its checks finish. Leave it for a later review pass.
5. Repository-level enforcement (a ruleset requiring Aquarium regression,
   Motor Gate and Mesh contract checks) is an owner setting; this note is the
   interim agent-side policy only and does not replace it.
