# Project #1 read-only board audit (Issue #23, R0.1)

This optional developer-only Python 3.11+ tool audits the **existing** personal GitHub Project at https://github.com/users/Garyu-tt/projects/1/views/1. It never creates a project, status field, card, repository attachment, workflow or GitHub API mutation. It does not run inside the Aquarium.

## Workflow

- With separately authorized, pre-existing GitHub CLI (gh) login that has read access to Projects v2: PYTHONPATH=src python -m mojum.project_v2_board --live. GitHub CLI authentication is external to Mojum; no token/password is passed as an argument, copied into prompts or saved by the module. If access is missing, this command fails closed and the normal repository Issues/PR/CI workflow remains authoritative.
- For offline audit: PYTHONPATH=src python -m mojum.project_v2_board --snapshot /path/to/graphql.json. Accepts one response or a list of paginated responses from the exact GitHub GraphQL selection in project_v2_board.QUERY.
- Optional --candidates /path/to/candidates.json: a JSON array of {"kind":"issue","number":23,"url":"https://github.com/Garyu-tt/mojum/issues/23"} (or pr + /pull/N). Candidates must be gathered from a separately verified repository snapshot. Missing candidates are reported as a **preview**, not added. No status assignment is guessed.

The report includes actual Status option IDs and names (no hard-coded Backlog/Review/Done mapping), existing Mojum issue/PR cards, duplicate-card warnings and missing-card previews. Non-Mojum project items are counted but neither detailed nor exported. Unknown Status choices, foreign project owner, malformed pagination and mixed revisions fail closed. JSON hashes or IDs are **not** proof of authorization, merge or readback.

## Remaining manual/independent acceptance gates

1. A Project-authorized owner confirms the live field options, repo linkage and native Auto-add rules in Project settings. Repo-only API access is insufficient to assert Project settings. No default status transitions until the actual field option IDs are accepted.
2. Backfill missing items via a separately reviewed, authorized API/UI operation with idempotent uniqueness checks, then rerun this audit to verify readback. If duplicates exist, resolve them first. This module intentionally does not implement write actions.
3. Review transitions must use exact-head PR CI + separate-author QA evidence; do not mark unresolved parent feature issues Done merely because one slice merged. The protected main ruleset is never bypassed by board metadata.
4. Never export PRIVATE cards, comments, tokens, organism logs or GitHub auth state into CI/public artifacts. This tool projects target repo identifiers only; an authorized GraphQL account may still read the whole user's board as part of API access.

## Verification

PYTHONPATH=src python -m unittest discover -s tests -p 'test_project_v2_board.py' -v

The tests are fully offline, use stubbed GraphQL responses, and verify strict project identity, actual status options, bounded pagination, target-repo filtering, duplicate detection, preview-only missing items, and CLI exit codes. A successful local unit test is not evidence that Project #1 was accessed, mutated or linked remotely. No new mandatory package or GPU requirement.
