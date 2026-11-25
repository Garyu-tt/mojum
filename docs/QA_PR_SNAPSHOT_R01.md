# QA PR Snapshot Admission R0.1

`admit_pr_snapshot` accepts one caller-supplied public pull-request metadata mapping and returns a detached frozen `PRSnapshot` plus a SHA-256 consistency digest.

## Contract

- Python standard library only; no HTTP client, GitHub API, credentials, SQLite, filesystem, subprocess, or model calls.
- Exact allowlist; PR bodies, titles, diffs, URLs, tokens, journal fields and unknown keys are rejected.
- Strict native types (notably bool is not accepted as an integer), bounded refs/strings/dependencies, timezone-aware ordered timestamps and 40-character lowercase commit SHAs.
- Refs are branch names under `refs/heads`: UTF-8 Unicode, `+`, `@` and a dot at the end of an intermediate path component are valid; Git's forbidden characters, hidden or `.lock` components, traversal, the reserved `HEAD` name, non-UTF-8 surrogates and a trailing dot on the full name are rejected.
- Dot-prefixed repository names such as `.github` are accepted; `.` and `..` components remain invalid.
- Merged snapshots must be closed. The upstream draft flag is preserved on closed snapshots, including historical merged records; it is metadata, not permission to merge a draft PR.
- Dependency IDs are unique, sorted in the result and cannot refer to the snapshot itself.
- Digest is canonical over normalized metadata. It is a consistency witness, **not** a GitHub signature, proof of freshness, reviewer approval or merge authorization.

## Run

```sh
PYTHONPATH=src python -m unittest tests.test_qa_pr_snapshot -v
```

The caller must obtain and authenticate upstream GitHub evidence separately. This module performs no polling and does not establish whether a PR is safe to merge.
