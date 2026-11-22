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
- Inventory is a **separately captured snapshot** of ChatGPT
  automation metadata, *not* a continuous direct API connection.
  It contains labels, enabled flags, last and next scheduled run
  times, but no prompts, credentials, run output or model data.
  It is mode 0600; the public fleet output removes automation IDs.
  A source inventory older than 2 hours is visibly marked stale.
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

Twenty-seven hermetic tests passed for fleet collector, Telegram
read-only bot and gateway. The tests cover no invented external
liveness, expected results for bad/stale inputs, source privilege
isolation, honest unavailable model endpoints, private atomic
snapshot output, non-owner command rejection and denial of mutation
paths. Tests require no bot token and make no network calls.

## Remaining synchronization limitation

**Full live cross-platform worker synchronization still requires an
explicit heartbeat from each ChatGPT/external task executor.**
ChatGPT's schedule metadata has no running-task lease here, and the
Cezar UI **must** display `external_running_confirmed:null` (unknown)
until this is available. The collector is continuous for host
model slots, Cezar runs and public GitHub view, but not for the
independent ChatGPT Automations registry, which currently has a
timestamped source snapshot. Do not treat a scheduled or recent
run as live. Do not infer running workers from PRs or available
model capacity. Preferred next work is a least-privilege,
source-authenticated start/heartbeat/end registry with a TTL and
independently verified source identity, not automatic merge or
permission-sharing with the bot.

## Safety

The Cezar UI, status snapshot and relay stay on pinned SSH loopbacks.
Do not bind them to `0.0.0.0`. Protect the source registry
`/var/lib/cezar/home/fleet/automations.json` with mode 0600.
No deployment credential is part of this repository. Actual Cezar
agent launch/admission, independent QA, exact HEAD/BASE CI and merge
remain separate and are **not** bypassed by visibility code.
