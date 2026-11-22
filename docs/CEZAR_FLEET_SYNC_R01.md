# Cezar Fleet Visibility R01: Scheduled agents ≠ active runs

## Root cause

The built-in Cezar 0.20.0 dashboard counts only Cezar-owned
`GET /api/v1/runs` tasks. On the inspected live server it had four
historical runs (3 cancelled, 1 failed), **zero running**. Yet the owner
also has five enabled, independently scheduled Mojum automations in
ChatGPT: **four developers (peer 1/5 to peer 4/5) and one independent
QA**. No peer 5/5 automation was found. These scheduled jobs run in
ChatGPT's separate execution environment, not in the Cezar process.

The machine also runs five llama.cpp inference endpoints. At first
verification they provided **17 physical model slots** and zero occupied
slots. Model slots are resources, not agents; the absence of local
inference is not proof that agents running elsewhere are idle.
GitHub open PRs are output artifacts, not proof of currently running
jobs. The owner was right: the original panel's "0 active" conflated
different systems. **Do not replace it with "5 active"** without
external executor start/end heartbeats.

## Production implementation

- `ops/cezar/fleet/collector.py` executes every 60 seconds in a
  constrained oneshot systemd service,
  `cezar-mojum-fleet-r01.service`, supervised by
  `cezar-mojum-fleet-r01.timer`.
- Reads only Cezar loopback `/api/v1/runs` and `/api/v1/github`
  (native, tokenless, bounded public PR view), model loopback
  `/slots` at ports 48081..48085, and a trusted owner-private
  minimal ChatGPT Automations inventory at
  `/var/lib/cezar/home/fleet/automations.json`.
- Inventory is a **timestamped ChatGPT Automations snapshot**, not
  a continuously authenticated live executor feed. It contains labels,
  enabled flags, last and next scheduled runs, and the recorded status
  (e.g. completed) of the last run. It contains no prompts, credentials,
  run result content or model data. It is mode 0600; the public fleet
  output removes automation IDs. A snapshot older than two hours is
  visibly marked stale. The initial five detailed run statuses were
  independently queried through Automations.peek and were all
  `completed` at observation time, not `in_progress`.
- Produces owner-only atomic JSON at
  `/var/lib/cezar/home/fleet/current.json` and a public-within-
  loopback Cezar static view at
  `/var/lib/cezar/runtime/node_modules/@open-mercato/cezar/web/dist/assets/cezar-fleet-r01.json`.
  It retains separate fields: `cezar.active`,
  `schedules.enabled` / `developers_enabled` / `qa_enabled`,
  `models.inference_busy_known` / `capacity_slots_known`,
  and `github.open_prs`. If a probe fails it returns an explicit
  unavailable source, never zero.
- `ops/cezar/fleet/overlay.js` is a small read-only supplementary
  UI appended to the already-running Cezar built `index.html`.
  A fixed bottom-right **Флот** control expands details,
  last/next schedule run times and evidence sources. The script
  uses same-origin JSON with a cache-busting query, updates when
  visible every 60 seconds and never alters existing Cezar React
  internals or the native Cezar "active" count. The original index
  was saved at
  `/var/lib/cezar/home/fleet/index-original-r01.html`.
  The web distribution is third-party generated output; a later
  npm upgrade may require reapplying this overlay. Do not modify
  `node_modules` without a verified backup.
- The existing **GET-only**, private SSH-bridged Cezar Telegram
  gateway now serves `/api/v1/fleet` from the same verified
  owner-private file. The `Cezar | Mojum` bot provides `/fleet`,
  a new inline **Флот** button, and a corrected `/status`
  explaining **"активных ВНУТРИ Cezar"**. Its Telegram command menu
  is owner-scoped. It cannot write Cezar tasks, approve or merge PRs.

## Live verification (2026-11-22)

The server collector and the laptop's existing private Cezar URL
`http://127.0.0.1:14329/p/mojum/` both returned the same strict
snapshot schema. First result: 5 enabled ChatGPT schedules (4 Dev +
1 QA), 0 active Cezar-owned runs, 0 / 17 occupied local model slots,
and 44 open GitHub PRs. As new PRs arrived, the live number rose to
45 without adding false "running agents". The SSH-bridged
`mk-neurocms` Telegram bot was tested against the real gateway
and returned the same breakdown; owner Telegram menu now has 11
commands. POST to the gateway is still denied with HTTP 405.

Thirty-four hermetic tests passed for fleet collector, Telegram
read-only bot, gateway and reversible overlay installer. The tests cover no invented external
liveness, expected results for bad/stale inputs, source privilege
isolation, honest unavailable model endpoints, private atomic
snapshot output, non-owner command rejection and denial of mutation
paths. Tests require no bot token and make no network calls.

## Remaining synchronization limitation

**Full live cross-platform worker synchronization still requires an
explicit heartbeat from each ChatGPT/external task executor.**
ChatGPT's schedule metadata has no live lease that our server
can directly subscribe to. Cezar therefore **must** display
`external_running_confirmed:null` (unknown) until a verified
executor heartbeat exists. The collector runs every minute for local
model slots, Cezar runs and the public GitHub view.

A separately requested new hourly ChatGPT sync automation could not be
created: the account's five-task scheduling limit was already in use.
Instead, the existing **peer 1/5** automation was updated, preserving
all original BUILD/QA/SHIP instructions, to **attempt** a best-effort
update of the owner-private ChatGPT schedule snapshot through the
authorized JediControl/YodaBR host. This is not proof that the scheduled
worker has the necessary tool access until a later successful refresh
is observed. The UI intentionally marks stale source data rather than
pretending to have full synchronization.

Do not treat a scheduled or recent run as live. Do not infer running workers from PRs or available
model capacity. Preferred next work is a least-privilege,
source-authenticated start/heartbeat/end registry with a TTL and
independently verified source identity, not automatic merge or
permission-sharing with the bot.

## Reproducible read-only UI installation

The source-controlled `ops/cezar/fleet/install_overlay.py` installs
`overlay.js` into the existing private Cezar `web/dist/assets`
directory and adds a single deferred script tag to its HTML shell.
It preserves the pre-injection HTML in the 0600 backup and refuses
symlinks, public writable locations, duplicate tags or an unrelated
post-upgrade index with an old backup. On a clean authorized host,
after separately reviewing the actual installed Cezar version:

```sh
# Run as the existing Cezar user mirbi, not as root.
python3 -B ops/cezar/fleet/install_overlay.py
```

On the current production host the installer also passed an
**idempotency** check against the already-installed overlay:
`was_present=true`, original backup preserved. Seven
`test_install_overlay.py` regression cases cover safe installation
and safe refusal. The fleet collector, bot and gateway have separate
reproducible source and tested systemd units in this PR. The bot
and Cezar data remain on their existing private SSH loopbacks.

## Safety

The Cezar UI, status snapshot and relay stay on pinned SSH loopbacks.
Do not bind them to `0.0.0.0`. Protect the source registry
`/var/lib/cezar/home/fleet/automations.json` with mode 0600.
No deployment credential is part of this repository. Actual Cezar
agent launch/admission, independent QA, exact HEAD/BASE CI and merge
remain separate and are **not** bypassed by visibility code.


## R02: do not confuse historical Cezar runs with GitHub task lifecycle

The native Cezar 0.20.0 *Runs/Tasks* view only includes tasks submitted to its
own /api/v1/runs. The currently configured resources.maxParallel=2 limits
those native runs; it does **not** enumerate five external ChatGPT schedules,
all independent GitHub PR authors or five llama.cpp servers with 17 physical
inference slots. Increasing this number is not a substitute for real provider
adapters and authenticated executor heartbeats.

The read-only fleet observer now provides separate, linked GitHub sources:
- Native /api/v1/github?limit=50 open Issue/PR counts and up to 12 latest
  open PRs plus 8 open Issues, with public GitHub record links.
- A separately pinned, no-proxy, no-redirect public REST GET to only
  /repos/Garyu-tt/mojum/pulls?state=closed&sort=updated&direction=desc&per_page=20.
  An owner-only 0600 cache throttles it to one request per 15 minutes. Each
  returned PR is reported merged only when merged_at exists; otherwise
  it is closed-unmerged. This is the latest 20 records, not all completed
  tasks or total throughput. On API outage stale cache is **explicitly**
  shown as stale; if unavailable it is not called zero.
- New-source fields are additive inside cezar.fleet.visibility.v1; consumers
  using the older schema keep the existing independent fields. Private
  agent names remain only in owner-local telemetry, with no private prompts,
  tokens or journal data. The overlay renders all titles through DOM
  textContent, and GitHub URLs are constructed only from checked integer IDs.
- The pinned, visible Mojum fleet control explicitly shows external schedule
  count, open PR count, and **Cezar-only active** count. Expanding it exposes
  recently opened and recently merged/closed PRs, open Issues, scheduled
  developers/QA and observed inference slot occupancy. It does NOT write
  ghost tasks into Cezar to make its native run count look larger.

**Outstanding limit:** actual live ChatGPT/external worker concurrency remains
unknown until independently authenticated start/heartbeat/end events are
delivered, with expiry and per-executor identity fencing. The existing
scheduled-task inventory is a timestamped snapshot, not such a source.
The GitHub evidence proves work artifacts and merges, never live agent
processes. The native OpenCode model/backend must be independently load-tested
and routed before resources.maxParallel is safely raised.

Run observer tests via:

python3 -m unittest discover -s ops/cezar/fleet -p test_*.py -v

On an operator-approved existing host, first check installed script hashes,
owner and service units. Reinstall the read-only overlay using the existing
idempotent installer and roll back to the saved original index on mismatch.
Do not touch the Cezar native run ledger, unpause the automation templates,
expose the loopback service publicly, or deploy a new scheduling authority.


### Browser immutable-cache repair and live rollout (2026-11-22)

The Cezar 0.20.0 static asset server replies with
Cache-Control: public, max-age=31536000, immutable for JavaScript, even
though the HTML shell itself is no-cache. Replacing cezar-fleet-r01.js in
place while leaving its old script URL in index.html therefore does not
force already-open browsers to load the updated UI.

The R02 installer accepts exactly one old or new known script marker,
migrates the previous unversioned path to a versioned
/assets/cezar-fleet-r01.js?v=r02 URL, and rejects mixed/duplicate tags.
It keeps the original HTML backup untouched, with private modes, and
is idempotent on repeated runs. Both query-versioned JS and the HTML shell
were verified through the actual loopback Cezar HTTP server.

An authorized operator performed a read-only source baseline check
against existing PR #688, saved two owner-private backups, and replaced
only the observer and static JS via atomic writes with owner mirbi and
mode 0600. The collector unit was run successfully. Its real snapshot
contained four historical native Cezar runs (zero active), five enabled
ChatGPT automations (no live-status claim), 47 open PRs, 34 open Issues,
and 20 recently updated closed PRs including three mergers in that
limited sample. The count is point-in-time, not a guaranteed latest value.

The first rollout encountered a root-owned /var/lib/cezar parent:
unprivileged atomic replacement was refused *before* changing the live
source. The already-created backups were validated, and the specific
owner-preserving atomic replacements were completed under the existing
authorized root control plane. No Cezar process restart or native
automation enablement occurred. The cache-busting HTML migration ran
through the unprivileged installer with the original backup preserved.

The ChatGPT Automations inventory was subsequently refreshed by the
connected first-party task reader (five last runs completed at capture).
That produces a fresh historical snapshot, not a live cross-platform
heartbeat; this limitation remains unresolved. End-to-end tests:
30/30 hermetic fleet cases and Node 22 syntax check passed on the
source-pinned private QA checkout before the HTML installation.

Rollout requires no local Cezar task deletion, GitHub credentials or
listener changes. The operator's private laptop URL retains the same
path; a normal page reload should request the no-cache HTML and the
new versioned script. Never expose this unauthenticated loopback UI on
a public interface.
