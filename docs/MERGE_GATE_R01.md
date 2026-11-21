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

- A requirement names one exact job, for example `aquarium-stdlib (3.12)`.
  Use `workflow / job` to match the workflow and the job fields together.
  Repeat `--require` for every expected job in every required matrix.
- The gate compares selectors literally. Supply no globs or standalone workflow
  names. Obtain the expected jobs from reviewed workflow definitions or branch
  protection. A live rollup can omit jobs that have yet to register.
- `success` and `neutral` are passing. `skipped` is rejected unless the same
  exact selector appears in `--waive JOB=REASON`.
- A waiver never masks `failure`, `cancelled`, `timed_out`, `action_required`,
  `stale`, `error` or `startup_failure`.
- A missing required job yields `defer` (`MISSING_REQUIRED_CHECK`).
  An unmatched selector with glob characters yields `reject`
  (`AMBIGUOUS_REQUIRED_PATTERN`).
- A check whose recorded `headSha` differs from the snapshot head yields
  `defer` (`STALE_CHECK`).
- `--expected-head-sha` is mandatory and must be a complete 40- or 64-digit
  hexadecimal SHA without whitespace or padding. Abbreviated SHAs are not
  acceptable for an exact-head acceptance decision; missing values reject
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

## Recommended merge sequence

```sh
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)
PYTHONPATH=src python3 -m mojum.merge_gate \
  --pr "$PR" --expected-head-sha "$SHA" \
  --require 'Mojum R0.3 online-learning aquarium regression / aquarium-stdlib (3.11)' \
  --require 'Mojum R0.3 online-learning aquarium regression / aquarium-stdlib (3.12)' \
  --require 'Mojum R0.3 online-learning aquarium regression / aquarium-stdlib (3.13)' \
  --require 'Mojum motor-gate independent QA / motor-gate-stdlib (3.11)' \
  --require 'Mojum motor-gate independent QA / motor-gate-stdlib (3.12)' \
  --require 'Mojum motor-gate independent QA / motor-gate-stdlib (3.13)' \
  --require 'Mojum mesh contract independent QA / mesh-stdlib (3.11)' \
  --require 'Mojum mesh contract independent QA / mesh-stdlib (3.12)' \
  --require 'Mojum mesh contract independent QA / mesh-stdlib (3.13)' || exit $?
gh pr merge "$PR" --squash --match-head-commit "$SHA"
```

`--match-head-commit` makes GitHub refuse the merge if the head moved between
the verdict and the merge call. The gate's exit code is the decision: `0`
proceeds, anything else stops the shell chain. This is a client-side guard; it
does not replace repository rulesets, which remain authoritative.

## Limitations

- Reconcile the job lists with current workflow definitions before reuse. The
  gate verifies the supplied jobs and relies on the caller for that inventory.
- The snapshot is a point-in-time read. The gate cannot prove that no later
  push occurred; pair it with `--match-head-commit` and re-run immediately
  before merging.
- `gh pr view` reports the rollup GitHub considers current. A rollup entry with
  an explicit `headSha` different from the PR head is treated as stale, but
  entries without `headSha` are trusted to belong to the rollup's head.
- Skipped-check waivers are caller-supplied and are only as honest as the
  caller. They are echoed in the output for audit.
- The module performs no merge and has no write path.
