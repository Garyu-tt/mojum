# Atomic GitHub issue reservation R0.1 (opt-in)

Issue #966, parent #478. GitHub issue comments and labels are not atomic test-and-set operations: 20 simultaneous agents can read "unclaimed" and each reserve the same task. GitHub REST `POST /git/refs` provides an atomic create-if-absent operation for a dedicated issue number.

## Invocation

```bash
# Provide a repository-scoped token with Contents: write via a secret manager.
export GITHUB_TOKEN='…'
# Pin a real, trusted, existing 40-hex main commit (sample below is illustrative only).
python tools/github_issue_claim.py --repo Garyu-tt/mojum --issue 966 --sha 0123456789abcdef0123456789abcdef01234567
```

The tool attempts exactly one HTTPS `POST /repos/{owner}/{repo}/git/refs` containing `{ "ref": "refs/heads/agent-claims/issue-966", "sha": "<commit>" }`. A 201 with an exact matching ref/commit returns exit **0** (`ACQUIRED`). A 422 triggers one read-only `GET /git/ref/heads/agent-claims/issue-966`; only when that exact commit ref exists does it return exit **2** (`BUSY`). All other HTTP statuses, malformed JSON, rate limits, authorization failures, redirects and transport errors return exit **1** (`ERROR`). No retries, force updates, deletion or merges.

Workers should create their *own child issue* under shared parent #478, then atomically reserve that child before committing changes. Parent work can have many independent child issues. After success, add a human-readable issue comment describing branch, exclusive files/scope and QA handoff. A GitHub branch is a persistent advisory semaphore, **not** a lease with expiry, heartbeat, proof of human identity or fencing token. Stale locks require explicit owner review and manual release; no automatic stale-claim takeover is implemented.

## Safety limits

- Python 3.11+ standard library only; explicitly opted in. Never installs dependencies, executes models, touches Aquarium or Noosphere, or changes shared Spartan/Cezar scheduler.
- `api.github.com` is hard-coded; owner/repo slugs, positive 31-bit issue ID, 40-hex SHA and ASCII token format are validated before any I/O. Base SHA is caller-pinned, never fetched from a moving ref.
- Ten-second HTTPS request timeout, 8-KiB bounded JSON responses, strict duplicate-key and ref/commit identity rejection. HTTP redirects are denied to avoid forwarding the Authorization header.
- Credential is read from `GITHUB_TOKEN` environment only; don't put it in shell arguments, logs or comments. Do not run this with fork/untrusted PR credentials.
- Write access and branch rulesets may prevent creating refs; never downgrade to non-atomic label/comment claiming on error.
- Cooperating agents get a single winner from GitHub's server-side create-ref semantics, but users with repository write access can intentionally move/delete the branch. This is **not** a cryptographic lock or distributed fleet scheduler.

## Offline regression

```bash
python -m unittest discover -s tests -p 'test_github_issue_claim.py' -v
```

The 12 test methods use a scripted in-memory HTTP boundary, including create success, competing claim, missing ref, hostile JSON, bad SHA, unauthorized, rate limit, malformed token and redirect. No external network or credentials. Authenticated end-to-end ref tests should use a disposable owner-approved issue, and are not automatic.
