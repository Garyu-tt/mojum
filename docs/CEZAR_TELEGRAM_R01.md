# Cezar Telegram R01: single-source ClaudeFabric replacement

## Incident and root cause

On 2026-11-22 the owner's old **ClaudeFabric** chat was still receiving
obsolete "ALT GitHub: cycle completed / blocked" and "GitHub API limit,
retry in one hour" messages. On `mk-neurocms`, the legacy
`alt-github-agent.timer`, `alt-github-notifier.timer` and
`alt-github-notifier.path` independently invoked
`/opt/alt-github-agent/worker.py` and `notifier.py`, while the separate
`claude-factory.service` long-polled Telegram. Systemd logs confirmed
issues #376, #129 and #415 were blocked with `RemoteDisconnected`
around 20:31 UTC, then notifier sent the old-format message. This
was unrelated to the active Cezar GitHub tab and admission registry.

## Cutover performed

On `mk-neurocms`:
- Backed up the old code, env and small state/offset/event files,
  root-only mode 0600:
  `/var/backups/cezar-telegram-migration-r01/claudefabric-legacy-config-20261122.tar.gz`
  SHA-256 `5993f67a50e7357d28076e1b21c0cafe5d8fd0d59c8823c0ce0da7704f2fea70`.
  Do not copy this backup into the repository: it contains credentials.
- Disabled and stopped **both** ALT timers and the notifier path,
  preserving old data in cold storage. Disabled and stopped the old
  `claude-factory.service` before starting a replacement, so only one
  Telegram bot process long-polls a given Telegram bot token.
- Created isolated user `cezar-telegram` and owner-only state directory
  `/var/lib/cezar-telegram`. Migrated **only** `TG_BOT_TOKEN` and
  `TG_ADMIN_ID` to root-only `/etc/cezar-telegram/telegram.env` without
  exposing their values. The new service does not receive GitHub,
  Anthropic, Claude or other provider secrets.
- Installed `/opt/cezar-telegram/bot.py`, supervised by
  `cezar-telegram-r01.service`, restricted to owner's Telegram private
  chat. Its Telegram profile was changed to **Cezar | Mojum** and the
  owner-scoped command menu updated.
- Saved the first snapshot, existing run statuses, bot update offset and
  the initial PR-creation time in mode-0600 state. First startup did
  **not** send historical PRs, stale ALT tasks or old rate-limit events.
  New PR alerts require both a higher PR number and `createdAt` **after
  the actual replacement startup**, preventing cached preexisting PRs
  from being misreported as new. The activation message is one-shot.

On `mirbi-linux`:
- The canonical Cezar 0.20.0 instance remains on loopback
  `127.0.0.1:4329`, with its own existing GitHub adapter and admission.
- A *separate read-only* Python HTTP gateway binds
  `127.0.0.1:4331`, supervised by
  `cezar-mojum-telegram-readonly-r01.service`. Only safe GET requests
  for `/api/v1/health`, `/api/v1/runs`, bounded
  `/api/v1/github` and `/api/v1/github/checks` are forwarded. No
  POST, Cezar automations mutation, arbitrary paths, redirect following
  or GitHub auth is exposed.
- The pinned SSH reverse loopback tunnel
  `cezar-mojum-neurocms-link-r01.service` maps Cezar gateway
  `127.0.0.1:4331` to private `127.0.0.1:14331` on `mk-neurocms`.
  **Neither endpoint should be bound to 0.0.0.0 or placed on public
  HTTP**: the read-only gateway is defense-in-depth, not an Internet API.

## Owner Telegram commands

`/status` returns live Cezar version, repo and counts of open PRs,
Issues, running and historical tasks. `/prs` lists the latest PRs
across agents, `/pr N` shows an open PR, `/ci N` reports Cezar's
cached check glyph, `/runs` and `/run UUID` inspect Cezar-owned runs,
`/refresh` refreshes Cezar's list, and `/alerts` toggles alerts.
`/help` explains the boundary. Inline buttons expose frequent reads.

**Cezar is the sole source of truth.** No Telegram command starts its
own ALT/model workers, assigns Github Issues, generates branches or PRs,
or merges. The bot does not impersonate the independent QA gate, and
Cezar CI glyph caching is *never* authorization to merge. External
GitHub PRs are visible without being misclassified as Cezar runs.

The bot sends deduplicated owner-only digests for PRs actually created
since startup and changes in Cezar run states. It does **not** send the
old timer's hourly "blocked" or rate-limit spam. A Telegram message
already present in the chat history is not retroactively deleted by
disabling the old sender.

## Verification

The production cutover was checked via pinned SSH and service status:
- New `cezar-telegram-r01.service` active; legacy
  `claude-factory.service`, ALT agent/notifier timers and notifier path
  inactive.
- New private bot state has `primed=true`, `announced=true`,
  saved Telegram update offset, startup cutoff, mode 0600, and
  real Cezar statuses. Owner received the one-time activation notice.
- `mk-neurocms` retrieved Cezar 0.20.0 health, 39 open PRs,
  19 Issues and PR #612 CI via the pinned SSH tunnel at inspection.
- `tests/test_bot.py` and `tests/test_gateway.py` cover owner-only
  commands, no replay, stale-cache cutoff, alarm dedup, one-shot
  update offsets, unauthorized users, write denial and allowlisted paths.
  17/17 hermetic offline tests passed; no Telegram credentials needed.

## Recovery and safeguards

The source-controlled units are templates. Deploy only to the
already-authorized hosts with the pinned SSH aliases and checked
systemd user, never by copying credentials into the repository.
To inspect health:

```sh
# On mk-neurocms
systemctl status cezar-telegram-r01.service
systemctl is-active claude-factory.service alt-github-notifier.path

# On mirbi-linux
systemctl is-active cezar-mojum-telegram-readonly-r01.service
systemctl is-active cezar-mojum-neurocms-link-r01.service
curl -fsS http://127.0.0.1:4331/api/v1/health
```

Do not run the old factory and the new bot with the same Telegram token
simultaneously. If rollback is required, stop and disable the new bot,
then **explicitly** restore the old service from trusted root-only
backup; do not re-enable the obsolete ALT timers without a separate
source and authority review. Prefer retaining old source archives until
the new operator interface passes independent QA.

The new bot is a *read-only* Telegram client of Cezar, not a privileged
administrative endpoint. Operational changes, code generation, release
acceptance and merge remain inside the independent Cezar/GitHub gates.
