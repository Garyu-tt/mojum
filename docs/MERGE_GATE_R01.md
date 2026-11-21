# Exact-head CI merge gate (R0.1)

`mojum.merge_gate` is an offline, deterministic guard for PR acceptance
(issue #415). It answers one question: **may this exact head be merged onto
this exact current base, given the CI evidence for that head?** It never
merges, never pushes, never calls a write endpoint, and needs no third-party
packages.

The merge API is not proof that PR-triggered workflows finished. The gate reads
a snapshot, checks the recorded head SHA against the SHA the caller intends to
merge, checks that the PR's recorded base commit is still the current target
commit, and returns `ready`, `defer` or `reject` with machine-readable reasons.

## Verdicts

| Verdict | Exit | Meaning |
| --- | --- | --- |
| `ready` | 0 | Every required check passed in the supplied head rollup, the PR base SHA equals the independently read current target SHA, and GitHub reports a clean merge state. |
| `defer` | 2 | Evidence is incomplete or stale: a check is queued/running/missing, the PR was tested against an older base, the PR is a draft, or mergeability is unknown. Re-query after updating the branch when necessary. |
| `reject` | 1 | Evidence is bad: head/base identity is missing or contradictory, conflicts exist, or a required check failed, was cancelled, timed out, or was skipped without a waiver. |

`reject` wins over `defer`: a failing check is never hidden by another check
that is still running. `INVALID_INPUT` (unreadable snapshot, malformed waiver,
failed `gh` call) is also a `reject` and never a `ready`.

### Check rules

- For `main`, the gate always requires successful Aquarium, Motor Gate, and
  Mesh jobs for Python 3.11, 3.12, and 3.13. Those workflows run on every PR.
  The gate identifies each job by its exact `workflow / job` label.
- Each `--require` adds an exact `workflow / job` label for a GitHub Actions
  job, or a bare name for a legacy commit status or external check which has
  no workflow identity. Use it for every applicable extra check. For a target
  other than `main`, supply the entire required inventory via `--require`.
  A bare Actions job name is rejected because another workflow can publish
  the same name.
- Wildcards (`* ? [ ]`) are rejected with `AMBIGUOUS_REQUIRED_PATTERN`.
  Workflow-only names are not job selectors and cannot certify a partial
  matrix. Missing required jobs defer with `MISSING_REQUIRED_CHECK`.
- Obtain the extra job inventory from reviewed workflow YAML or protection
  rules. The live rollup may omit jobs that have yet to register.
- The nine built-in jobs require `success`. Extra jobs can also pass with
  `neutral` or an explicit `--waive JOB=REASON` for `skipped`.
- A waiver never masks `failure`, `cancelled`, `timed_out`, `action_required`,
  `stale`, `error` or `startup_failure`.
- A required job that matches nothing yields `defer`
  (`MISSING_REQUIRED_CHECK`), because its workflow may not have registered yet.
- Multiple rollup entries for one required identity yield `defer`
  (`AMBIGUOUS_REQUIRED_CHECK`); the caller must not guess which run is current.
- A check whose recorded `headSha` differs from the snapshot head yields
  `defer` (`STALE_CHECK`).
- `--expected-head-sha` requires a full 40- or 64-character Git object ID.
  Omitting it returns `EXPECTED_HEAD_REQUIRED`. `--expected-base` names a
  branch and defaults to `main`.
- Base evidence uses two full object IDs: `baseRefOid` is the base commit
  recorded for this PR/check cycle, while `currentBaseRefOid` (or
  `--expected-base-sha`) is an independent read of the target branch now. A
  mismatch returns `STALE_BASE`, even when GitHub reports
  `mergeStateStatus=CLEAN`; update the PR branch and rerun CI.
- `--pr` obtains both base IDs through read-only GitHub API calls and rejects a
  head or branch that changes between the rollup and PR metadata reads.
- For non-`main` targets, an empty inventory returns `NO_REQUIRED_CHECKS`.

## Offline usage

Supply a snapshot containing both `headRefOid` and `baseRefOid`, then evaluate
it without network access:

```sh
PR=419
gh pr view "$PR" --json number,headRefOid,baseRefName,headRefName,isDraft,mergeable,mergeStateStatus,statusCheckRollup > pr-view.json
PR_BASE_SHA=$(gh api "repos/{owner}/{repo}/pulls/$PR" --jq .base.sha)
CURRENT_BASE_SHA=$(gh api repos/{owner}/{repo}/git/ref/heads/main --jq .object.sha)
jq --arg pr_base "$PR_BASE_SHA" --arg current_base "$CURRENT_BASE_SHA" \
  '. + {baseRefOid:$pr_base,currentBaseRefOid:$current_base}' \
  pr-view.json > pr.json
PYTHONPATH=src python3 -m mojum.merge_gate \
  --input pr.json --expected-head-sha "$(git rev-parse HEAD)" \
  --expected-base-sha "$CURRENT_BASE_SHA"
```

An offline `pr.json` must contain `baseRefOid` in addition to the normal
`gh pr view` fields. It should also contain an independently captured
`currentBaseRefOid`; the explicit `--expected-base-sha` above can supply that
second value instead. Missing base evidence fails closed.

`--input -` reads the snapshot from stdin. `--pr N` fetches it through `gh`
(opt-in network). Output is one JSON object on stdout:

```json
{"schema":"mojum.merge-gate.v1","verdict":"defer","code":"CHECK_PENDING",
 "exit_code":2,"head_sha":"...","base_sha":"...","reasons":[...],
 "merge_performed":false}
```

### Accepted input shapes

Enriched `gh pr view --json` output (camelCase, `baseRefOid`,
`currentBaseRefOid`, `statusCheckRollup`, `CheckRun` and `StatusContext`
entries) and a snake_case fixture form (`head_sha`, `base_ref`, `base_sha`,
`current_base_sha`, `checks`) are accepted. This keeps tests hermetic while
the live CLI can read the extra base identities itself. Input is capped at
4 MiB.

## Check a live PR rollup

Include additional applicable jobs with `--require`.

```sh
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
BASE=$(gh api 'repos/{owner}/{repo}/git/ref/heads/main' --jq .object.sha)
PYTHONPATH=src python3 -m mojum.merge_gate \
  --pr "$PR" --expected-head-sha "$SHA" --expected-base-sha "$BASE"
```

Treat a `ready` result as a prerequisite for the
[merge acceptance policy](MERGE_ACCEPTANCE_POLICY.md). Verify required
check-run provenance against that policy before submitting a merge guarded by
`--match-head-commit`. The policy also calls for a GitHub repository ruleset to
enforce the requirements at merge time.

## Limitations

- Update the built-in inventory when the Aquarium, Motor Gate, or Mesh workflow
  names, job names, or matrices change. Supply path-filtered requirements for
  each PR based on the applicable workflow definitions.
- GitHub branch rulesets enforce repository-level policy independently of the
  local gate. Use an up-to-date base rule to protect against base movement.
- The gate records a point-in-time head and base verdict. The target branch can
  still move after a client-side read, so re-run immediately before merging
  with `--match-head-commit`; only a repository ruleset closes that server-side
  race completely.
- Offline mode trusts the supplied `baseRefOid`; generate it from a read-only
  current-ref query and re-run immediately before merging with
  `--match-head-commit`.
- `gh pr view` reports the rollup GitHub considers current. A rollup entry with
  an explicit `headSha` different from the PR head is treated as stale, but
  entries without `headSha` are trusted to belong to the rollup's head.
- Skipped-check waivers are caller-supplied and are only as honest as the
  caller. They are echoed in the output for audit.
- The module performs no merge and has no write path.
