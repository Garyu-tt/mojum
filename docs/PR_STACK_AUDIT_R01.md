# Offline PR stack dependency DAG audit R01

Related: #886 (subtask of #478). This **read-only, offline** precursor helps workers avoid unreviewable stacked-PR dependency graphs. It does **not** call GitHub, issue tokens, claim live tasks, approve PRs, or override the independently enforced exact-head CI/ruleset gate (#415).

## Run

```bash
python tools/pr_stack_audit.py /path/to/explicit-pr-snapshot.json
python -m unittest discover -s tests -p 'test_pr_stack_audit.py' -v
```

Exit `0` means the supplied *static* graph passed its declared dependency consistency checks, **not** that a PR can merge. Exit `1` means graph blockers; exit `2` means invalid/unreadable input. JSON is emitted to stdout, with fixed schema `mojum.pr-stack-audit/1`; scripts must check exit status as well as `ok`.

## Input contract

Input is a normalized, operator-provided **synthetic or trusted export**, not GitHub API raw JSON. It has schema `mojum.pr-stack-snapshot/1`, a canonical target `base_branch` and up to 512 `pull_requests`. Each PR contains exactly `number`, `state` (`open`, `closed`, `merged`), `head_ref`, lowercase 40-digit `head_sha`, `base_ref`, `depends_on` (array of PR numbers), `base_pr` (number or null) and `parent_head_sha` (SHA or null). A `base_pr` must identify the PR whose **head branch is the actual Git base** for this child, and must appear in `depends_on`. If `base_pr` is null, `base_ref` must be `base_branch` and `parent_head_sha` must be null. Logical dependencies that do not determine the Git base may appear in `depends_on` independently.

Example (all refs and SHAs here are synthetic):

```json
{
  "schema": "mojum.pr-stack-snapshot/1",
  "base_branch": "main",
  "pull_requests": [
    {"number": 10, "state": "open", "head_ref": "build/parent", "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "base_ref": "main", "depends_on": [], "base_pr": null, "parent_head_sha": null},
    {"number": 11, "state": "open", "head_ref": "build/child", "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "base_ref": "build/parent", "depends_on": [10], "base_pr": 10, "parent_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
  ]
}
```

The tool checks duplicate identifiers and head refs, self/missing/closed dependencies, base declaration mismatches, stale pinned parent SHA, merged/closed base PRs, cycles and stable dependency-before-dependent ordering. A merged **logical** prerequisite may be considered satisfied when the child is already rebased onto `main`, but a merged **base** parent remains a blocker until rebased/retargeted. It does not inspect whether a Git ref actually exists; the source snapshot must be refreshed and authenticated by its collector.

## Safety and budgets

- Exact JSON object keys and native types only. Duplicate JSON keys are rejected, rather than silently last-wins.
- Maximum input read is **1 MiB + 1 byte**, checked on actual bytes read; at most 512 PRs, 64 dependencies per PR and 4096 total edges; SHA and ref format bounds enforced.
- JSON numeric literals are bounded before integer allocation; Python bool is not accepted as a PR number. Stable order ensures reproducible findings independently of input entry order.
- No credentials, network, shell, repository mutations, or automatic merge authority. Findings contain only numeric PR IDs and fixed diagnostic codes (no untrusted field values or private journal content).
- Requires Python 3.11+, stdlib only, synthetic regression tests. It is not evidence of live concurrent agents, actual GitHub parent SHA verification, successful CI, lease/fencing or 300-worker throughput.
