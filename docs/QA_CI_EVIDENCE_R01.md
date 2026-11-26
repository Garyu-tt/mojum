# QA exact-head CI evidence evaluator R0.1

## Purpose

`mojum.qa-ci-evidence/1` is an opt-in, pure-standard-library evaluator for a complete caller-supplied GitHub Actions snapshot. It fails closed unless every declared required check appears exactly once, completed successfully, and references the exact PR head SHA.

## Input contract

`evaluate_ci_evidence(snapshot, now=...)` accepts a native mapping with exactly:
- `schema`: `mojum.qa-ci-evidence/1`
- `complete`: literal boolean `true`
- `pr_head_sha`: lowercase 40-character commit SHA
- `required_checks`: 1–128 unique bounded check names
- `checks`: exactly one record per required check
- `observed_at`: canonical UTC timestamp `YYYY-MM-DDTHH:MM:SSZ`

Each check record contains exactly `name, head_sha, run_id, status, conclusion, completed_at`. Only `completed` + `success` is accepted. Evidence older than seven days, completion timestamps materially in the future, mismatched heads, duplicate/missing/extra checks, unknown fields and malformed values are rejected.

## Output and trust boundary

A successful result contains a deterministic SHA-256 consistency digest over canonicalized evidence and the decision `PASS`. Input order does not affect the digest. The evaluator does not fetch or authenticate GitHub data, prove snapshot completeness, inspect branch protection, validate reviews, approve PRs, or authorize merging. The caller must establish provenance and completeness independently. Empty GitHub status lists must never be converted into a successful empty matrix.

## Run

`PYTHONPATH=src python -m unittest tests/test_qa_ci_evidence.py`

No network, subprocess, credentials, SQLite, Noosphere, external dependency or production deployment.
