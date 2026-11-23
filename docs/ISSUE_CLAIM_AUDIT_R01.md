# Mojum issue claim collision audit R0.1 (issue #786 / parent #478)

This is a **read-only, offline advisory report**, not a distributed lock or a
GitHub-backed reservation. It does not contact GitHub, inspect worktrees, sign
or authenticate commenters, start agents, decide mergeability, or change CI.
Claims without structured markers are reported for manual review rather than
interpreted as evidence that a task is free. Concurrent snapshot races remain
possible; agents must recheck issue comments/PRs before claiming work.

## Explicit claim marker

Place exactly one standalone line in a GitHub issue body or comment, **outside** Markdown fenced code blocks, blockquotes and indented code:

```text
<!-- mojum-claim/1: {"worker":"worker-20261123-abc","scope":"issue-478/claim-audit","action":"claim"} -->
```

To release a claim, the same **GitHub author** and `worker` post another
comment for the same `scope` using `"action":"release"`. A repeated claim
from the same worker is idempotent; a different author cannot release its
claim. This is only an audit convention: worker IDs and comment bodies are
self-asserted, and edited comments are not protected by this tool. The scope
is a stable, specific, case-sensitive slug; umbrella issues can have multiple
non-overlapping scopes. No wildcard matching or semantic inference is done.

## Input

A trusted operator exports a bounded JSON snapshot of GitHub issue bodies
and comments, preserving authenticated GitHub comment IDs/authors and original
UTC creation timestamps (the audit itself does **not** fetch or authenticate
this source). Example:

```json
{
  "schema": "mojum.issue-claim-export/1",
  "repository": "Garyu-tt/mojum",
  "issues": [
    {
      "number": 786,
      "author": "Garyu-tt",
      "created_at": "2026-11-23T12:00:00Z",
      "body": "Initial issue text (may include one claim marker)",
      "comments": [
        {
          "id": 111,
          "author": "Garyu-tt",
          "created_at": "2026-11-23T12:05:00Z",
          "body": "<!-- mojum-claim/1: {\"worker\":\"agent-A\",\"scope\":\"issue-786/audit\",\"action\":\"claim\"} -->"
        }
      ]
    }
  ]
}
```

`body`, `author`, and `created_at` on the issue itself are optional **together**;
`comments` may be omitted for an empty array. The tool accepts up to 2 MiB,
300 issues, 20,000 total comments, and 16 KiB per text body; malformed data,
duplicate JSON keys, duplicate issue/comment IDs, non-UTC timestamps, invalid
Unicode (including unpaired UTF-16 surrogates), incomplete issue-body metadata,
and malformed/quoted/fenced machine markers fail closed. Code examples must
never count as active claims. It does not accept source URLs, tokens,
or private organism data.

## Run

```bash
python3 tools/issue_claim_audit.py --input /path/to/issue-comments.json
python3 -m unittest discover -s tests -p 'test_issue_claim_audit.py' -v
```

Output is canonical JSON, ordered by issue number and scope. Exit status:
- `0`: **clear**, no detected conflicts or unresolved legacy claim hints;
- `1`: **collision** or **needs_review**, never safe to assume a task is free;
- `2`: **invalid** input (includes oversized/unreadable JSON).

Collision means at least two **distinct claimed worker IDs** for the exact
same `(issue,scope)` at snapshot time. `manual_review` identifies unstructured
legacy `CLAIMED`/🔒 hints, actor mismatches and unmatched releases. A single
syntactically valid claim is not cryptographic proof of worker identity,
exclusive ownership, live work or fresh lease. A clean snapshot is never
permission to merge and never substitutes for independent exact-head QA.

This is stdlib-only on Python 3.11+, CPU-first, offline, and intentionally
does not change canonical Aquarium, Noosphere journals, Graph IR or Cezar.
