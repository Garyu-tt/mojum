# Mojum guarded auto-ship R0

An opt-in GitHub Actions scheduled publisher for **already independently approved** PRs. This replaces repetitive manual waiting, **not** branch ruleset #27857245. It never changes settings, grants bypass, force pushes, executes untrusted PR scripts, or merges without Github server authorization.

- Runs from default-branch workflow every five minutes (GitHub schedules are best-effort) and supports manual dry-run dispatch.
- Reads up to 100 public open same-repo PRs, rotates across at most 18 eligible candidates per cycle. Draft and fork PRs excluded.
- Requires clean mergeability, exact main and head SHA, base ancestor current (zero behind), separate final approving review, all nine Aquarium/Motor/Mesh Python 3.11–3.13 check-runs with **matching head SHA**, and all other present exact-head checks completed SUCCESS.
- Requires zero unresolved inline review threads and complete single-page thread enumeration. Review/query/CI errors fail closed. GitHub also enforces current last-push independent approval.
- Immediately rechecks PR HEAD, base SHA, mergeability and current main SHA; attempts `squash` with GitHub's expected `sha` fence. Only one successful merge per cycle: next PR is reverified after main moves.
- Records holds in the Actions log without advertising them as merge successes.

Limitations: the runner token must be permitted by repository Actions settings to merge under the live branch ruleset. It does **not** update stale branches automatically, does not provide the independent reviewer identity, and cannot itself approve PRs. GitHub Actions schedules may be delayed, throttled or disabled when unused. This workflow must first merge into main by existing independent QA; until then it does not run.

Reviewers must assess that a new GitHub Actions writer workflow is appropriate to this owner repo, then verify a manual dry-run before relying on its scheduled merges.
