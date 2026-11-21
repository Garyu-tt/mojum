# R0.14 compatibility candidate: archive-only v2A in the v2B/v3 host

**Draft, not a canonical release decision.** This isolated branch is based on [PR #125](https://github.com/Garyu-tt/mojum/pull/125) exact commit `463caec4b1d17bf978622814c377ffcf007e326c`. No source owner PR or `main` is modified.

## What the conflict means

Original [#112](https://github.com/Garyu-tt/mojum/pull/112) journals use `motor_policy=local_cell_v2` and `mojum.developmental.explorer.local-cell/2`. New [#115](https://github.com/Garyu-tt/mojum/pull/115) / [#125](https://github.com/Garyu-tt/mojum/pull/125) journals use `developmental_policy=local_cell_v2` and `mojum.developmental.local-cell/2`. These are not the same event protocol. #125 also supports separate v3 LRU/HMAC experiments.

This branch preserves **all three** without giving two incompatible persisted implementations the same runtime selector:

| Explicit selector | Wire format | Ability |
| --- | --- | --- |
| `legacy` | Existing R0.13 scripted or coarse_v1, unchanged | Original writable behavior |
| `local_cell_v2` | #115/#125 v2B | Original writable behavior |
| `local_cell_v3` | #122/#125 v3B | Original writable behavior |
| `local_cell_v2_a_archive` | Original #112 v2A, pinned `motor_policy` manifest | **Existing DB only, deterministic replay/checkpoint; no append, acquire or new signed cache** |

No migration, rewriting, silent coercion, renamed historic event or automatic selection. This is an experimental API; **only the owner and independent review can designate the final canonical v2 ABI**.

## SQLite read-only enforcement

For archive mode, `LocalNoosphere` opens a pre-existing file with SQLite URI `mode=ro` and `PRAGMA query_only=ON`. No initialization DDL, chmod, PRAGMA WAL conversion or SQLite append is requested; `append` is refused before validation. The constructor checks exact owner/schema metadata without modifying it and closes the connection synchronously if initialization fails.

`Aquarium` validates the existing v2A manifest and every event through full canonical JSON/hash-chain audit and deterministic replay using a distinct versioned v2A policy implementation copied from exact #112. `step`, `acquire` and `save_replay_snapshot` are refused; optional HMAC cache input is rejected. If the file does not exist, archive mode refuses to bootstrap it.

SQLite WAL may create auxiliary shared-memory/read locks on supported filesystems; this is a read-only *journal operation* guarantee, not a guarantee that the filesystem produces zero temporary metadata. No event content is printed by the CI.

## Reproduction (separate immutable #112 source checkout)

Dedicated GitHub Actions matrix checks out #112 at `8d2eaf234c09177ae0b92dfef4bb41950529c912` and uses its actual CLI to create a private synthetic SQLite journal. The candidate host separately replays it, verifies the checkpoint, policy schema, stored count/head and full original row fingerprint, rejects writes and wrong B/v3 policy, catches a deliberately ambiguous JSON record on a **disposable copied DB**, and permits the original A writer to resume afterwards. No production journal, real Android, user files, private network, MuJoCo, neural brain or physical body is tested.

For a **pre-existing local journal only**:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./PRIVATE-original-v2a.sqlite3 \
  --exploration-mode goal_free --developmental-policy local_cell_v2_a_archive \
  --ticks 0
```

All other pinned organism configuration parameters (genome, graph, room, start, dt, neural/sensory modes) must match the archived bootstrap; any mismatch fails closed. The CLI's default `--ticks 120` is intentionally invalid for archival mode; users must supply `--ticks 0`.

## Acceptance boundaries

The original source schema and metadata stay forever attributable to their originating version. This bridge is **read-only archival continuity**, not full cross-writable interchange or a migration. The cross-wire [release gate #123](https://github.com/Garyu-tt/mojum/pull/123) / [#126](https://github.com/Garyu-tt/mojum/pull/126) remains **blocked** until a genuinely unified version strategy is owner-approved, independently reviewed and tested. Key limitations include unauthenticated local hash-chain without a trusted external head; optional HMAC snapshots in other policies do not make the A archives signed.

Do not merge this experimental branch to `main`, close [#116](https://github.com/Garyu-tt/mojum/issues/116), or claim mobile/background/real AI validation merely because CI is green.
