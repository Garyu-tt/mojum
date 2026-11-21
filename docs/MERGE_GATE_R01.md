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
| `ready` | 0 | Every required check completed successfully for the exact head, the base matches, and GitHub reports a clean merge state. |
| `defer` | 2 | Evidence is incomplete: a check is queued/running/missing, the PR is a draft, or mergeability is still unknown. Re-query later. |
| `reject` | 1 | Evidence is bad: head/base mismatch, conflicts, or a required check failed, was cancelled, timed out, or was skipped without a waiver. |

`reject` wins over `defer`: a failing check is never hidden by another check
that is still running. `INVALID_INPUT` (unreadable snapshot, malformed waiver,
failed `gh` call) is also a `reject` and never a `ready`.

### Check rules

- **Explicit inventory required:** each `--require` names ONE concrete job
  (e.g. `aquarium-stdlib (3.12)`) or an exact `workflow / job` label.
  Repeat `--require` for **every expected job in every required matrix**.
  Use `workflow / job` when names are reused across workflows. This syntax
  checks the workflow as well as the job name.
- Wildcards (`* ? [ ]`) are rejected with `AMBIGUOUS_REQUIRED_PATTERN`.
  Workflow-only names are not job selectors and cannot certify a partial
  matrix. Missing required jobs defer with `MISSING_REQUIRED_CHECK`.
- Obtain the expected job inventory from reviewed workflow YAML / protection
  rules **before** reading the live rollup; the rollup itself may omit
  not-yet-registered matrix siblings. A caller-supplied incomplete inventory
  is not a sufficient release gate.
- `success` and `neutral` are passing. `skipped` is rejected unless the same
  exact job selector appears in `--waive JOB=REASON`.
- A waiver never masks `failure`, `cancelled`, `timed_out`, `action_required`,
  `stale`, `error` or `startup_failure`.
- A required job that matches nothing yields `defer`
  (`MISSING_REQUIRED_CHECK`), because its workflow may not have registered yet.
- A check whose recorded `headSha` differs from the snapshot head yields
  `defer` (`STALE_CHECK`).
- `--expected-head-sha` requires a full 40- or 64-character Git object ID.
  Without it the verdict is `reject`
  (`EXPECTED_HEAD_REQUIRED`).
- No requirements at all is `reject` (`NO_REQUIRED_CHECKS`): a gate that
  requires nothing proves nothing.

## Offline usage

Snapshot a PR to JSON, then evaluate it without network access:

```sh
gh pr view 419 --json number,headRefOid,baseRefName,headRefName,isDraft,mergeable,mergeStateStatus,statusCheckRollup > pr.json
PYTHONPATH=src python3 -m mojum.merge_gate \
  --input pr.json \
  --expected-head-sha "$(git rev-parse HEAD)" \
  --require 'aquarium-stdlib (3.11)' \
  --require 'aquarium-stdlib (3.12)' \
  --require 'aquarium-stdlib (3.13)' \
  --require 'motor-gate-stdlib (3.11)' \
  --require 'motor-gate-stdlib (3.12)' \
  --require 'motor-gate-stdlib (3.13)' \
  --require 'mesh-stdlib (3.11)' \
  --require 'mesh-stdlib (3.12)' \
  --require 'mesh-stdlib (3.13)'
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

```sh
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
PYTHONPATH=src python3 -m mojum.merge_gate \
  --pr "$PR" --expected-head-sha "$SHA" \
  --require 'aquarium-stdlib (3.11)' \
  --require 'aquarium-stdlib (3.12)' \
  --require 'aquarium-stdlib (3.13)' \
  --require 'motor-gate-stdlib (3.11)' \
  --require 'motor-gate-stdlib (3.12)' \
  --require 'motor-gate-stdlib (3.13)' \
  --require 'mesh-stdlib (3.11)' \
  --require 'mesh-stdlib (3.12)' \
  --require 'mesh-stdlib (3.13)'
```

Treat a `ready` result as a prerequisite for the
[merge acceptance policy](MERGE_ACCEPTANCE_POLICY.md). Verify the current base
commit and required check-run provenance against that policy before submitting
a merge guarded by `--match-head-commit`. The policy also calls for a GitHub
repository ruleset to enforce the requirements at merge time.

## Limitations

- This client-side guard cannot discover a complete matrix inventory from an
  incomplete rollup; users must pin all expected job names from workflow YAML
  or branch protection. A green listed subset is **not** proof all jobs ran.
  Verify the declared inventory when workflows change.
- It does not configure or substitute GitHub `main` branch rulesets.
- The snapshot is a point-in-time read. The gate cannot prove that no later
  push occurred; pair it with `--match-head-commit` and re-run immediately
  before merging.
- `gh pr view` reports the rollup GitHub considers current. A rollup entry with
  an explicit `headSha` different from the PR head is treated as stale, but
  entries without `headSha` are trusted to belong to the rollup's head.
- Skipped-check waivers are caller-supplied and are only as honest as the
  caller. They are echoed in the output for audit.
- The module performs no merge and has no write path.
