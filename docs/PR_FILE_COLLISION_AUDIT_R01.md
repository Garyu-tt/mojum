# Spartan R0.1: offline PR changed-file collision audit

**Parent:** #478. **Claim:** #958. **Implementation:** tools/pr_file_collision_audit.py.
This is a read-only, deterministic, stdlib-only *snapshot auditor* for
multiple independent Mojum BUILD workers. It does not authenticate against
GitHub, create leases, verify code, stage a patch, execute a model or merge PRs.

## Why

The Spartan coordinator owns an exclusive *functional scope* per active task,
but two independently chosen scopes can still edit the same repository file.
Issue claim comments and the claim-collision auditor (#786) audit ownership
statements, not actual paths changed by open PRs. The patch guard (#523)
validates one candidate against its own path grant; it cannot know another
worker's simultaneous patch. This tool fills that independent gap.

## Strict input

Freeze an **explicit, complete** snapshot of the candidate open PRs at known
commit SHAs before auditing. This is a data format, not a GitHub API endpoint.
The collector is outside this tool's trust boundary and must independently
retrieve and authenticate the list of PRs, each base/head commit and the
**complete changed-path list**, without truncation. If a rename is present,
the collector must include both original and new paths. No claim of GitHub
currentness is made by this tool.

Example inventory.json:

~~~json
{
  "schema": "mojum.pr-file-collision-input/1",
  "prs": [
    {
      "number": 1001,
      "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "state": "open",
      "draft": true,
      "changed_paths": ["src/mojum/module_one.py"],
      "allowed_paths": ["src/mojum/module_one.py"]
    },
    {
      "number": 1002,
      "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "head_sha": "cccccccccccccccccccccccccccccccccccccccc",
      "state": "open",
      "draft": true,
      "changed_paths": ["src/mojum/module_one.py", "tests/test_two.py"],
      "allowed_paths": ["src/mojum/module_one.py", "tests/test_two.py"]
    }
  ]
}
~~~

SHAs and PR numbers above are **synthetic**, not an assertion about those real
PRs. The actual auditor accepts only exact full lowercase SHA-1 commit IDs.
Inputs are bounded: 256 KiB UTF-8 JSON, 1–300 open PRs, 1–32 changed files
and 1–32 granted files per PR, at most 8,192 changed path entries. Only
relative canonical repository paths (A–Z, a–z, digits, underscore, dot,
hyphen and slash) are admitted. No globs, parent/dot segments, absolute
paths, .git segments, repeated JSON keys, floating-point values, nulls in typed
fields, or unknown fields are accepted. Different spellings of the same path
are never silently normalized.

## Run

~~~sh
python3 tools/pr_file_collision_audit.py < inventory.json
python3 -m unittest discover -s tests -p 'test_pr_file_collision_audit.py' -v
~~~

The program prints **exactly one canonical JSON report** on stdout for valid
input. Exit codes: **0** = no file collision or scope violation in the supplied
snapshot, **1** = at least one finding, **2** = invalid/unsafe/incomplete input
(report withheld, sanitized error on stderr). Do not treat exit 0 as permission
to merge: independent QA still verifies exact HEAD+BASE, CI, reviewer
principal, branch rules, dependency DAG and the latest GitHub state.

The report schema mojum.pr-file-collision-report/1 contains:

- snapshot_sha256: SHA-256 of normalized and sorted validated snapshot
  metadata. Reproducibility receipt, **not** an authentication signature.
- checked_prs, checked_changed_paths: supplied inventory counts.
- same_path_collisions: paths changed by more than one PR, sorted PR IDs.
- file_directory_collisions: a changed file that is a directory ancestor
  of another changed file (also flags an impossible single-PR tree).
- out_of_scope: paths outside each PR's exact allowed-path list.
- clear: true only if all three findings arrays are empty.

Every finding is an **advisory conflict/coordination risk**, not proof of a
Git merge failure; stacked PRs can intentionally touch the same path.
All PRs, including drafts, are checked equally. No file contents, review
comments, tokens or PRIVATE Noosphere data enter the format or output.

## Integration boundary / non-goals

An authenticated collector must freeze changed paths from GitHub at exact
head_sha and base_sha, persist the original snapshot as an auditable CI input
and run this CLI before allocating overlapping code scopes or accepting PRs.
A collector that omits PRs or rename source paths can yield false negatives.
If large inventories require sharding, cross-shard overlap must still be
checked: independently clear shards do not imply global no-overlap.

No network, GitHub mutations, filesystem walks, patch application, SQLite
access, branch creation or model execution occur. The only input is the
explicitly piped stdin snapshot; stdout is JSON. The module does not edit
canonical Aquarium, GENESIS, Noosphere or shared Spartan coordinator files.
Independent QA and exact-head CI remain required before accepting the PR.
