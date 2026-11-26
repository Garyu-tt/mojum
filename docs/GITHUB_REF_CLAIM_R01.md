# Mojum GitHub ref claim R0.1

Scope: optional coordination helper for issue #478, not a deployed Spartan scheduler.

## Why

A "CLAIMED" issue comment is visible but **not an atomic lock**. Two ChatGPT
sessions can read the same old comment set and both announce ownership. Local
Spartan SQLite leases cover only a shared dispatcher database, not disconnected
ChatGPT workers. This helper uses GitHub's unique Git ref creation as the only
reservation decision. It is separate from #786's read-only collision audit.

## Safe runbook

1. **Manually inspect the issue, comments, existing PRs and prior ownership.**
   This R0.1 does not automatically migrate or override legacy comment claims.
   Pick a genuinely unclaimed, narrowly described scope and exact paths.
2. Preview without credentials, network, or repository mutations:

   ~~~bash
   python tools/github_ref_claim.py --repo Garyu-tt/mojum --issue 478 --scope my-new-subtask
   ~~~

3. With repository contents-write permission and the token provided **only via
   the host environment**, explicitly reserve:

   ~~~bash
   python tools/github_ref_claim.py --repo Garyu-tt/mojum --issue 478 --scope my-new-subtask --reserve
   ~~~

   The caller must preconfigure GITHUB_TOKEN in the environment; do not copy
   credentials into chat, files, logs or command-line arguments.

4. A successful GitHub POST /git/refs publishes an immutable *reservation name*
   refs/heads/claims/issue-478/my-new-subtask pointing to main's current
   commit. A best-effort issue comment announces it. Actual implementation
   occurs on a separate build branch; open a draft PR for independent QA.

## Outcomes

- dry_run (exit 0): pure local naming, **no claim**.
- reserved (exit 0): unique ref created, issue notice posted.
- reserved_comment_failed (exit 0): ref created but comment failed; **publish
  the ref manually**. The claim exists, do not retry creating it.
- taken (exit 3): the exact ref already exists, or a race created it first.
  **Select another scope/task**; never overwrite someone else's ref.
- error (exit 2): invalid input, auth/policy/transport failure, closed issue,
  ambiguous post-commit network fault, or other failure. Treat a timeout after
  POST as **unknown**, inspect the ref manually. Never blindly retry it.

Two participants using **the same canonical scope slug** cannot both create
its ref. Different slugs *can* semantically overlap: subtask coordination and
human/agent inspection are still necessary. A reservation is **not** a TTL
lease, does not authenticate a human owner, and does not automatically expire
or release. Legacy comment-only reservations are not imported.

## Security, privacy and limitations

- Only https://api.github.com is used; redirect following and environment
  proxies are disabled. No plaintext token is written to disk or output.
- No PRIVATE Noosphere data, Docker, actuator access, remote host commands or
  source-code execution. Stdlib only, Python 3.11+.
- Ref creation and comment publication are deliberately **two separate
  operations**. A comment failure cannot undo a successful reservation.
- HTTP 422 is reported as taken only if a follow-up read confirms the exact
  reservation ref. All other 422 causes remain errors.
- A ref records existence, not worker identity or proof of authorization; a
  privileged repository maintainer can forcibly edit/delete refs. Protect
  claims/** refs with an appropriate ruleset before relying on them broadly.
- This is a **building block**, not an authoritative global dispatcher,
  automatic expiry protocol, or proof that 300 workers are running.

## Test

~~~bash
python -m unittest discover -s tests -p 'test_github_ref_claim.py' -v
~~~

Tests use fake API responses, simulate two simultaneous agents, HTTP 422,
partial comment failure, timeouts, bad JSON and secret-bearing errors. No
GitHub network access and no optional runtime dependencies are required.
