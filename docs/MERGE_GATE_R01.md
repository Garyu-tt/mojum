# Exact-head CI merge gate (R0.1)

`mojum.merge_gate` is an offline, deterministic guard for PR acceptance
(issue #415). It answers one question: **may this exact commit be merged right
now, given the CI evidence for that commit?** It never merges, never pushes,
never calls a write endpoint, and needs no third-party packages.

The merge API is not proof that PR-triggered workflows finished. The gate reads
a snapshot, checks the recorded head SHA against the SHA the caller intends to
merge, and returns `ready`, `defer` or `reject` with machine-readable reasons.

## Verdicts

| Verdict | Exit | Meaning |
| --- | --- | --- |
| `ready` | 0 | Every required check passed in the supplied head rollup, the target branch matches, and GitHub reports a clean merge state. |
| `defer` | 2 | Evidence is incomplete: a check is queued/running/missing, the PR is a draft, or mergeability is still unknown. Re-query later. |
| `reject` | 1 | Evidence is bad: head/base mismatch, conflicts, or a required check failed, was cancelled, timed out, or was skipped without a waiver. |

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
- For non-`main` targets, an empty inventory returns `NO_REQUIRED_CHECKS`.

## Offline usage

Snapshot a PR to JSON, then evaluate it without network access:

```sh
gh pr view 419 --json number,headRefOid,baseRefName,headRefName,isDraft,mergeable,mergeStateStatus,statusCheckRollup > pr.json
PYTHONPATH=src python3 -m mojum.merge_gate \
  --input pr.json --expected-head-sha "$(git rev-parse HEAD)"
```

`--input -` reads the snapshot from stdin. `--pr N` fetches it through `gh`
(opt-in network). Output is one JSON object on stdout:

```json
{"schema":"mojum.merge-gate.v1","verdict":"defer","code":"CHECK_PENDING",
 "exit_code":2,"head_sha":"...","expected_head_sha":"...","reasons":[...],
 "merge_performed":false}
```

### Accepted input shapes

Both `gh pr view --json` output (camelCase, `statusCheckRollup`, `CheckRun`
and `StatusContext` entries) and a snake_case fixture form (`head_sha`,
`base_ref`, `checks`) are accepted. This keeps tests hermetic while the CLI can
be pointed at real PR data. Input is capped at 4 MiB.

## Check a live PR rollup

Include additional applicable jobs with `--require`.

```sh
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
PYTHONPATH=src python3 -m mojum.merge_gate \
  --pr "$PR" --expected-head-sha "$SHA"
```

Treat a `ready` result as a prerequisite for the
[merge acceptance policy](MERGE_ACCEPTANCE_POLICY.md). Verify the current base
commit and required check-run provenance against that policy before submitting
a merge guarded by `--match-head-commit`. The policy also calls for a GitHub
repository ruleset to enforce the requirements at merge time.

## Limitations

- Update the built-in inventory when the Aquarium, Motor Gate, or Mesh workflow
  names, job names, or matrices change. Supply path-filtered requirements for
  each PR based on the applicable workflow definitions.
- GitHub branch rulesets enforce repository-level policy independently of the
  local gate. Use an up-to-date base rule to protect against base movement.
- The gate checks the base ref name and records a point-in-time head verdict.
  Verify the base commit separately and re-run immediately before merging
  with `--match-head-commit`.
- `gh pr view` reports the rollup GitHub considers current. A rollup entry with
  an explicit `headSha` different from the PR head is treated as stale, but
  entries without `headSha` are trusted to belong to the rollup's head.
- Skipped-check waivers are caller-supplied and are only as honest as the
  caller. They are echoed in the output for audit.
- The module performs no merge and has no write path.
