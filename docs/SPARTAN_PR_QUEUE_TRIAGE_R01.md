# Spartan PR queue triage R0.1 (read-only)

This stdlib-only offline tool inventories an entire open PR queue in one pass.
It lets a separate QA agent move to another PR when CI is blocked. It does NOT
merge, approve, push, call GitHub or use any credentials.

Its status **candidate_for_independent_acceptance is not permission to merge**.
The exact-head/base acceptance policy, independently audited CI job inventory,
actual check-run SHA and merge-commit parent binding, review threads and
repository rulesets still apply under issue #415. This tool is one small
component of issue #478, not a deployed acceptance daemon.

## Snapshot format

    {
      "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "pull_requests": [
        {
          "number": 123,
          "headRefOid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "baseRefOid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "baseRefName": "main",
          "isDraft": false,
          "mergeable": "MERGEABLE",
          "mergeStateStatus": "CLEAN",
          "statusCheckRollup": [
            {"workflowName": "Aquarium", "name": "py311",
             "status": "COMPLETED", "conclusion": "SUCCESS",
             "headSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
          ]
        }
      ]
    }

For example, gather read-only snapshots with GitHub CLI and jq from a trusted
operator machine or separate read-only QA service, not inside a model/test job:

    set -euo pipefail
    BASE=$(gh api repos/Garyu-tt/mojum/git/ref/heads/main --jq '.object.sha')
    gh pr list -R Garyu-tt/mojum --state open --limit 500 --json number --jq '.[].number' |
      while IFS= read -r number; do
        gh pr view "$number" -R Garyu-tt/mojum \
          --json number,headRefOid,baseRefOid,baseRefName,isDraft,mergeable,mergeStateStatus,statusCheckRollup
      done > prs.ndjson
    jq -s --arg base "$BASE" '{base_sha:$base, pull_requests:.}' prs.ndjson > queue.json

Pass **every actual required CI job label** explicitly, including all Python
matrix members, from reviewed workflow YAML and live GitHub check-run names.
The following labels are only synthetic examples, not Mojum's audited inventory:

    python3 tools/pr_queue_triage.py --input queue.json \
      --require 'Aquarium / py311' \
      --require 'Aquarium / py312' \
      --require 'Aquarium / py313' > queue-report.json

If a caller omits required jobs or forgets which path-filtered workflow applies
to a PR, this triage result cannot prove complete acceptance. Never use
wildcards or workflow-only shortcuts to certify a CI matrix.

## Report semantics

- **blocked**: a required check has a non-success conclusion, even if another
  required check is missing or pending.
- **defer**: a draft, changed/wrong base, incomplete, duplicate or missing
  check, or uncertain mergeability/merge state.
- **invalid**: malformed snapshot, check entry or duplicate PR number.
  Invalid records do not stop processing other PRs.
- **candidate_for_independent_acceptance**: all caller-specified checks
  appear successful and snapshot metadata matches. Still requires separate
  HEAD+BASE verification, source review, audited CI inventory and branch rules.

A check's head SHA may be absent, or may name GitHub's synthetic PR merge
commit rather than its source head. Such labels appear in
unverified_check_heads. Independent acceptance must verify check-run commits
and parents as described in docs/MERGE_ACCEPTANCE_POLICY.md. A matching check
SHA alone does not prove the tested base SHA.

Bounds: 4 MiB input, 500 PRs, 512 checks per PR and 64 explicit unique
requirements. Globs are rejected. Exit 0 means a report was generated,
**never** that a PR may be merged; exit 2 means invalid input. Every report
includes merge_authorized=false and merge_performed=false.

Run isolated unit tests with:

    python3 -m unittest discover -s tests -p test_pr_queue_triage.py -v

This slice neither deploys any fleet, contacts API providers, nor modifies
PRIVATE SQLite journals, runtime state or GitHub branch protections.
