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
- The dedicated Cezar CPU Qwen2.5-Coder-3B endpoint was increased from
  8192 to 16384 tokens after a real 8535-token OpenCode request exhausted
  the prior context. Large prefill cost and model-authored pseudo-tool
  output remain observed; the larger context is **not** proof of correct code.
- Cezar currently has at most **one active local agent run** because
  the selected 16k-context model serves one physical API slot.
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

## Single Cezar instance and legacy deduplication (2026-11-22)

The Windows laptop `DESKTOP-PUHG2PP` previously had an additional
Cezar 0.20.0 launched through `npx cezar-run@0.20.0` in Ubuntu WSL,
listening on loopback port 4321 for the `mechanicus-director` checkout.
Its task queue had one completed task and no active tasks. The original
WSL package/profile and 38 Cezar-specific Windows helper scripts/logs
were removed **after** the historical state was archived and checksummed.
The **original NeuroCMS and Mechanicus repositories and their histories
were not deleted**. No task was imported into the Mojum queue.

There is now one running Cezar, the Linux server instance. It remains
bound to `127.0.0.1:4329`. A supervised SSH reverse loopback tunnel
(`cezar-mojum-laptop-link-r01.service`) makes it available **only on the
Windows laptop's loopback** at `127.0.0.1:14329`. The laptop's desktop
shortcut `Cezar - Mojum (server).url` points to
`http://127.0.0.1:14329/p/mojum/`; its health endpoint returned HTTP
200, reporting the expected server repo path. The SSH tunnel uses a
pinned known-host key, dedicated user key and automatic reconnect.
The UI has no native authentication: **never** bind it to `0.0.0.0`,
expose that port to the public Internet, or place it behind an anonymous
proxy.

The WSL backup contains task metadata for three historical projects.
To inspect it **without executing, extracting or re-enqueuing tasks**, use
`tools/cezar_legacy_history_index.py`:

```sh
# Paths on the server only, after creating the owner-only output directory:
python3 -m tools.cezar_legacy_history_index \
  --archive /var/lib/cezar/migrations/desktop-puhg2pp-cezar-20261122-r01.tar.gz \
  --output /var/lib/cezar/home/legacy-index/desktop-puhg2pp-task-dedup-r01.json
```

The index is owner-only mode 0600, with no raw prompts, titles, secrets,
model responses or imported runnable tasks. It uses SHA-256 of the exact
task text **and project name**; matching text from *different projects*
is intentionally **not** merged. Real snapshot evidence:

| Legacy project | Runs | Unique task texts | Extra duplicate runs |
| --- | ---: | ---: | ---: |
| `neurocms-r43-review-candidate` | 9 | 2 | 7 |
| `mechanicus-director` | 1 | 1 | 0 |
| `neurocms-dev-r43` | 3 | 2 | 1 |
| **Total** | **13** | **5** | **8** |

All 13 historical runs were marked `done`. The index source archive
SHA-256 is `4cc26a7e572b521338a3e9fa8c07588128ff94f515762dd84b168e204628357d`;
the 3 original backup archives remain owner-private under
`/var/lib/cezar/migrations`. The live Mojum Cezar queue was not
modified and had **zero active tasks** at this inspection. No claims
are made that historical task completions count as Mojum QA acceptance,
and no old worktree was automatically reactivated.

This history index proves archive duplicates and makes replay
visible; it is **not a live cross-agent admission lock**. Cezar 0.20.0
already has an append-only native automation receipt ledger and reserves
each `(automationId, eventId)` only once, including across restarts of
this single instance. Auditing the server's private
`.ai/cezar/automation-receipts.ndjson` showed **four receipt versions
for two unique event keys**, with exactly **one Cezar run ID per key**.
This prevents replay of the same native event by the same rule, but
**does not** prevent two different automations or manual task requests
from submitting semantically identical work. Cross-rule admissions
should additionally normalize GitHub repo + Issue/PR identity + intended
revision before unattended dispatch, and independently verify every
produced patch. Both native GitHub event automations remain paused
until the model quality and admission gates are established.

## Cross-rule admission and real Cezar model pilot (2026-11-22)

The native Cezar event scheduler has its own durable per-automation
`(automationId,eventId)` receipt ledger. That **does not** stop two
different automation rules from dispatching the same GitHub work.
For these Mojum CPU resources we have now introduced **one actual
dispatch path** for opt-in public Issue and PR review events:

- `tools/cezar_admission.py` keeps a private SQLite database with
  `BEGIN IMMEDIATE`, a unique SHA-256 key of
  `(repo,kind,number,revision,phase)` and an exact scope hash.
  It deliberately does **not** include the calling automation rule or
  model in the identity. Duplicate requests return the existing receipt
  and do not send a second `POST /api/v1/runs`. A failed/uncertain
  remote POST keeps its original reservation until manual reconciliation
  instead of risking a blind retry.
- The only model task admitted by this path is a bounded, read-only
  `quick-task` with `enforceReadOnly=true`, no autonomous approval,
  GitHub writer, commit, push or merge. The URL is pinned to Cezar's
  loopback API. `block` terminally rejects an untrusted/incorrect
  output, with the exact admission UUID and Cezar run UUID, without
  opening the same revision for automatic retries.
- `tools/cezar_intake.py` only scans public open Mojum Issues/PRs with
  the exact **`cezar-ready`** label. It excludes `cezar-hold`,
  `security` and `needs-human`. PR identity uses the exact 40-hex
  head SHA, while Issue identity uses its GitHub update revision.
  No issue body, HTTP authentication, model API token or shell command
  reaches the scheduler. A hard cap allows only **one unresolved**
  Cezar model task for this CPU profile.
- The versioned systemd service/timer in
  `ops/cezar/systemd/cezar-mojum-admission-r01.*` runs the pinned
  tested scripts every ~15 minutes under `mirbi`, with system files
  read-only, restricted writable state/prompt directories, memory/CPU
  caps, no laptop home or archived secrets. On the first live run it
  discovered **zero currently labeled candidates**, dispatched **zero**
  tasks and exited successfully.
- Both *native* Cezar GitHub event rules remain explicitly **paused**,
  to avoid bypassing the cross-rule gate. If another path is later
  enabled, it must route every dispatch through this common registry
  or the cross-rule deduplication promise will no longer hold.

Actual supervised pilot: an exact-source PR #612 review for head
`9b9a45d3f1252baff201d880edadd71325e11a70` created one Cezar
run `ba409c36`. Repeating that admission returned
`admitted:false,reason:already-claimed`, and the live Cezar task
list contained only one matching run. The read-only OpenCode
16k-context local Qwen2.5-Coder-3B eventually returned an answer,
but it **incorrectly invented a database race** that was already
prevented by `BEGIN IMMEDIATE` and cited unrelated SQLAlchemy.
Independent review therefore **rejected** the output, cancelled
the run and terminally marked its admission `blocked`.
A third submission for the exact same PR revision was refused.
**No code from the model was applied.**

Two direct Qwen2.5-Coder-3B unified-diff attempts were also rejected
by the existing `spartan_patch_guard.py` for incorrect syntax/scope.
A small real input-type validation issue found while checking the
new trusted admission code was fixed separately and covered by a
negative test. Thus these experiments verify **transport, dedup,
fail-closed model rejection and reproducibility**, but do **not**
demonstrate reliable autonomous code generation.

On the server, safe operational checks:

```sh
systemctl status cezar-mojum-admission-r01.timer
systemctl status cezar-mojum-admission-r01.service
python3 /var/lib/cezar/guarded-intake/cezar_admission.py \
  --db /var/lib/cezar/home/admission/mojum.sqlite stats
```

### Recovery when a POST outcome is unknown and no run ID was recorded

A timeout or malformed response can strand `claimed, run_id=NULL` despite a
possibly launched Cezar run. Ordinary `block` and `finish` only handle
submitted runs. **Never retry the POST.** An authorized operator must inspect
Cezar's live run list and independently establish that the matching task is
absent or stopped. Then explicitly mark the exact claim terminal:

```sh
python3 /var/lib/cezar/guarded-intake/cezar_admission.py \
  --db /var/lib/cezar/home/admission/mojum.sqlite \
  block-claimed --admission-id '<admission-uuid>' \
  --revision '<exact-revision>' --checked-cezar-runs
```

UUID, exact revision, owner-private SQLite and `claimed` with NULL run ID are
mandatory. The action is idempotent only for the already blocked same claim,
frees one-task capacity, and never replays the source revision. The flag is
a *human acknowledgement*, not evidence automatically obtained from Cezar.
Do not assert it if any matching task could still be active.

Only an authorized operator with access to the owner-private admission
database should mark a failed task blocked; never automatically mark
an unreviewed model answer `finished`. Once the exact Cezar run ID is
independently confirmed complete and its output reviewed, the same operator
may explicitly release the one-task admission ceiling without reopening the
same revision:

```sh
python3 /var/lib/cezar/guarded-intake/cezar_admission.py \
  --db /var/lib/cezar/home/admission/mojum.sqlite \
  finish --admission-id '<admission-uuid>' --run-id '<cezar-run-uuid>'
```

The `finish` operation checks that the admission is `submitted` with
the matching exact run UUID and is idempotent for that same `finished` run.
It refuses never-submitted, already-blocked and mismatched runs. The timer,
models and public GitHub metadata never invoke `finish`; manual independent
review is still mandatory. GitHub CLI on the server
still lacks write authorization. **The 300 Spartan names are logical
identities, not 300 verified concurrent, useful local inference workers.**
The QA gate remains separate from any eventual draft-PR publisher.

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
