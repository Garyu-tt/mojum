# Cezar ↔ GitHub staging for Mojum (R0.1)

This is a **reproducible, opt-in** handoff from GitHub issues and pull
requests to a self-hosted Cezar cockpit. Cezar orchestrates worktrees,
runs and review queues; GitHub **remains the source of truth** for source,
issues, PR HEAD/BASE, CI and merge decisions.

**Do not interpret these templates as a deployed, authenticated fleet.**
A local Cezar instance was installed on 2026-11-22, and the templates
have been created there **paused**. Authentication, trusted patch execution,
stronger model evaluation and independent QA still gate enabling them.

## Runtime receipt (2026-11-22)

- Cezar 0.20.0 and OpenCode 1.19.6 are installed on the operator's
  CPU-first Linux host, with a **loopback-only** cockpit and an isolated
  `mojum` checkout on a nonfull filesystem. The cockpit has no built-in auth;
  never publish it to the Internet without an authenticated front door.
- A separate systemd timer fetches `origin/main` every 10 minutes and
  performs only a clean, fast-forward local update. No forced reset, remote
  push, or automatic merge.
- An unauthenticated GitHub public REST metadata mirror updates every
  15 minutes. Its first snapshot had **18 open issues and 89 open PRs**.
  This is a **read-only snapshot**, not native Cezar task ingest or a PR publisher.
- The native `gh` CLI is present but **not authorized** on this host.
  Cezar's GitHub polling therefore reports `gh auth login` required.
  The ChatGPT GitHub connector cannot inject credentials into an unrelated
  host's CLI. Never put PATs, cookies, passwords or tokens into issue content.
- The dedicated Cezar CPU model has a larger 8192-token context. Real
  OpenCode smoke tests show a substantial prefilling cost and sometimes
  model-authored pseudo-tool output. That is not validated code.
- Cezar currently has at most **one active local agent run** because
  the selected 8k-context model serves one physical API slot.
  Spartan's 300 logical identities/other local slots remain separate.

## Source-controlled automation definitions

`ops/cezar/automations/issue-labeled.json`

- Poll `issue.labeled` every 300 seconds, **only** when the operator
  explicitly attaches label `cezar-ready`.
- Skip `cezar-hold`, `security`, and `needs-human`.
- Constrain matches and task size, create a dedicated worktree, report a
  test-first plan. Label changes and the issue body are **untrusted input**.
- No automatic acceptance or merge.

`ops/cezar/automations/pr-opened-qa.json`

- Poll new PR openings every 600 seconds.
- Triage exact HEAD/BASE and required CI only. No execution of code from
  untrusted PRs, no self-approval and no automatic merge.

Templates intentionally do **not** include provider credentials, `enabled`
flags, host-specific paths or a pending job backlog.

## Activation gate

On the Cezar host, after the operator independently authorizes `gh` for
**only the necessary Mojum permissions** (prefer a short-lived, fine-grained
token scoped to the specific repository, entered on the host, not in chat),
verify:

```sh
gh auth status
gh repo view Garyu-tt/mojum --json nameWithOwner
cez automation check <imported-issue-automation-id>
cez automation check <imported-pr-automation-id>
```

Cezar's `automation import ... --apply` imports these templates **paused**.
The operator then enables **one** rule at a time only after a genuine
model response, isolated worktree and independent exact-head QA pass.
Use `cez automation list` and `cez automation pause <id>` for control.
A successful query or existing model slot is not a proof of working coding
automation. GitHub write access belongs to a constrained PR publisher,
separate from model execution.

## Required release invariants

1. Unique GitHub Issue/PR identity, deduplicated transitions and a
   bounded queue; no duplication of development tasks across hosts.
2. Cezar model worker has no host-wide GitHub token or merge permission.
3. Untrusted responses are validated against exact issue scope. No
   model-authored shell command or patch is applied without isolation.
4. Tests and CI attach to the exact tested PR HEAD and current BASE.
   Independent QA can block or reject. Only the release gate may merge.
5. Report **accepted PRs/hour**, resource usage, queue latency, live
   physical model calls and blocked tasks separately from logical agents.

See #478 and #415. Keep GitHub as source of truth while Cezar manages
local execution and review work.
