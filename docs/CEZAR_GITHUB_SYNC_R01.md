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
  A deliberately restricted **public-read `gh` adapter**
  (`tools/cezar_gh_public_read.py`) is available in the Cezar service's
  private `PATH`. It can only read the pinned public Mojum repository via
  approved `gh repo view --json nameWithOwner` and `gh api GET`
  search/timeline calls. It does not authenticate, handle other repositories,
  execute GitHub writes, or grant PR publishing privileges.
- Native Cezar `automation check` now works through that adapter: 0
  `cezar-ready` Issue label events and 5 recent PR open candidates
  (truncated at the configured cap). **Both automations remain paused**.
  The user-facing real `gh auth status` still says unauthenticated.
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

## Read-only adapter deployment and release gates

For a dedicated Cezar systemd service, place the version-controlled
`tools/cezar_gh_public_read.py` into a **private owner-owned executable**
path such as `/var/lib/cezar/readonly-bin/gh`, ahead of `/usr/bin`
in **only that service's** `PATH`. Do not modify the system GitHub CLI.
The adapter's cache must be owner-private; all network requests are
fixed to `https://api.github.com`, use no token, and reject unexpected
commands, headers, repos, HTTP methods, input sizes, and redirects.
The service must itself remain bound to loopback; it has **no built-in auth**.

Read-only event previews are possible before GitHub login:

```sh
cez automation check <imported-issue-automation-id>
cez automation check <imported-pr-automation-id>
```

The tests in `tests/test_cezar_gh_public_read.py` exercise the
API allowlist, fail-closed write denial and bounded cache without network.
This adapter is intentionally **not** `gh auth login`; genuine GitHub
PR publishing and any other write operations still require separate
operator authorization of the actual `gh` CLI and narrow write grants.
Never add tokens to Cezar's model-running environment.

Both native Cezar event automations remain **paused** until the operator
also verifies a useful local model answer (not a tool-call hallucination),
an isolated worktree, test execution and independent exact-head QA.
Only then enable one automation at a time, measure actual quality, and
ensure the provider stays within verified inference capacity.

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
