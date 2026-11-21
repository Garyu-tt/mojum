# Mojum R0.21 — authenticated cold recovery from an archived copy

**Research Draft** stacked on [#157](https://github.com/Garyu-tt/mojum/pull/157)
(copies owner-HMAC verified, selected sealed+tail history between distinct
filesystem mounts). No production node, physical device, original user
Noosphere history, connected Google Drive, cloud or main branch is changed.

## Missing acceptance condition

R0.20 copies a selected immutable PRIVATE history after HMAC verification,
but previously a recovery operator needed to manually extract and type
the source head to run `restore-point`. A forged archive could trick an
operator into using a self-declared hash unless they carefully checked
the separate owner-HMAC receipt first.

R0.21 adds `recover_pinned_replica`, a one-shot CPU-only fail-closed
cold restore command whose sole trust input is a **separately pinned
receipt SHA256** and the **separate owner signing key**. The recovered
source is **only an historical point-in-time copy**, not an autonomous
canonical writer or live failover.

## Pre-write authorization

- Require `allow_private_restore=True` or CLI `--include-private`.
- Require exact `expected_agent_id` (the intended actor), not an
  inferred agent ID from the possibly tampered physical manifest.
- Check an existing 0700 archive, *new absent* SQLite target in a
  separate owner-private directory, and forbid restoration inside
  archive tree or storage of key/receipt inside replica tree.
- Verify receipt canonical JSON and HMAC against a 32-byte key and
  independently pinned owner receipt SHA256. A newer or older signed
  receipt is NOT accepted without the operator's pinned digest.
- Verify **all** physical sealed and incomplete tail segments on
  the source archive against the signed statement, before creating
  any recovery journal or copying PRIVATE event payload.
- Reconstruct **only** the exact signed source history in a new
  owner-private scratch SQLite and verify global SHA-chain and absolute
  event count. BEFORE the final file is visible, commit a separate
  `mojum.noosphere.recovered-quarantine/1` custody row pinned to the
  actor, exact event count, source head and independently pinned
  receipt hash. Commit SQLite INSERT-rejection triggers for
  `segmented_events` and `segments`, checkpoint WAL and only then
  atomically publish the new SQLite path. The newly published DB
  is independently verified again.
- A default `SegmentedNoosphere.append` or `Aquarium.step` against
  this restored copy **must fail without committing any new event**:
  `QUARANTINED_READ_ONLY` marks the copy as a historical record,
  not a writer. No silent promotion.
- These SQLite triggers are a local **accidental-write guard**,
  not tamperproof security against the database owner or OS admin,
  who could DROP them. A separately reviewed owner approval plus
  writer fencing/authority protocol is still required for safe
  promotion after real source failure.
- Never remove, repair, truncate, rename, silently merge or promote
  a pre-existing canonical Noosphere source database.

An owner HMAC is integrity, not encryption or physical independence.
The trusted receipt hash must have already been stored outside the
archive's failure domain. If an attacker controls the original key
and the external expected digest, rollback and forgery remain possible.

## Offline CLI

```bash
PYTHONPATH=src python -m mojum.segment_archive_cli recover-replica \
  --archive-dir /separate-mounted-volume/private-archive-B \
  --new-db /owner-private/NEW-recovered.sqlite3 \
  --receipt-file /owner-private/receipts/source-at-tick-X.json \
  --key-file /owner-private/keys/owner-32-byte.key \
  --expected-receipt-sha SEPARATELY_PINNED_RECEIPT_SHA256 \
  --expected-agent-id dante-fixture-001 \
  --include-private
```

Summary stdout includes event count and hashes only, not the
PRIVATE original event payload, private source or key file paths.
The recovered PRIVATE SQLite file is **plaintext**: encryption at
rest is still a separate required security control.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_verified_replica_restore.py -v
```

Tests use only disposable owner-private synthetic fixtures:

- 130 actual SQLite source records: 128 sealed + 2 live tail,
  owner-HMAC signed head, exact byte copy to second folder, then
  original disposable SQLite and first archive are made unavailable.
  Recover and hash-verify all 130 records from the second archive
  WITHOUT using original source or first archive.
- Actual 130 synthetic Aquarium motor ticks + bootstrap = **131
  source records**; after original synthetic source and first archive
  disappear, reconstruct the second copy and cold replay to exactly
  the previous Aquarium checkpoint. The restored Aquarium is a
  **quarantined reader**, and trying to advance another frame must
  fail without changing its history.
- Dedicated Python 3.13 stress executes **10,050 actual synthetic
  v4 motor frames** with `toy_probe_v2`, a durable epoch crossing
  at tick 9999 and more than 4,000 measured contacts. The resulting
  journal has **10,052 source records**, exported as **8192 sealed
  + 1860 live tail**. After original synthetic SQLite and primary
  archive paths are made unavailable, owner-HMAC signed cold recovery
  from the second copy must reproduce the exact Aquarium checkpoint,
  epoch and learning associations while refusing any new frame append.
- Quarantine custody row has immutable UPDATE/DELETE triggers; a
  local SQLite admin can deliberately drop these, so they prevent
  unintentional mutation rather than supply a network fencing lease.
  Injection of an interruption before final atomic publish verifies
  that no writable recovered destination becomes visible.
- Real CPU subprocess using `recover-replica` with a refusal
  without `--include-private`, then successful explicit restore.
- Wrong independent receipt digest, wrong HMAC key, wrong actor,
  corrupted/missing archived member, 0755 restore destination,
  existing SQLite path, target/credentials in archived tree,
  and repeated attempt to overwrite the restored source all fail
  closed. Tests also confirm a recovered SQLite and Aquarium
  **reject further writes**, keep the original checkpoint, and an
  interruption before final publication does not expose an
  unfenced database.

CI matrix CPython 3.11–3.13 plus parent HMAC and owner-verified
cross-filesystem transfer regression suites. No actual user data,
server, phone, physical motor, or connected node is accessed.

## Remains outside scope

This code does not automatically choose which source is most recent,
does not compare two competing writer forks, does not acquire a
lease/quorum, does not provide automatic replication, encryption,
uninterruptible operation or demonstrate recovery from a failed
real physical drive. Distinct `st_dev` values by themselves do
not prove independent power/host or durable backup.

Do **not** promote the recovered source into canonical writer
status without explicit owner acceptance and fencing protocol.
Real multi-host replication, independent receipt custody and
partition fencing remain open in
[Issue #141](https://github.com/Garyu-tt/mojum/issues/141).
Keep Draft and require exact SHA CI plus independent privacy review
before merge or production use.
