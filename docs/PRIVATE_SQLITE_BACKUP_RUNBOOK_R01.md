# PRIVATE SQLite journal: offline backup and restore runbook (R0.1)

Status: operator procedure for the **single-host synthetic Aquarium**, not a tested disaster-recovery guarantee. This document does not change the journal format or implement replication. Applies only to journals that the operator owns and is authorized to inspect.

## Safety boundary

- Treat the journal, its backups, and temporary copies as **PRIVATE**. Never upload them to issues, PRs, CI artifacts, public datasets, chat transcripts, or logs.
- Prefer a stopped Aquarium/portable-host process. Do **not** copy the live `.sqlite3` file with `cp` or Explorer: SQLite may have outstanding WAL transactions or sidecar files. Do not assume a filesystem snapshot is transactionally consistent.
- Preserve the original database until the restored copy has been independently verified. Never restore over the only surviving original.
- A successful SQLite `integrity_check` proves structural consistency, **not** application-level event-chain validity, correct policy identity, confidentiality, or recoverability of an organism.
- The backup is local and unencrypted unless the operator separately uses authorized encryption-at-rest. SHA-256 is an accidental-change detector, not authentication.
- Stop all writers before final cutover. Do not run two Aquarium writers against the same path or merge divergent histories.

## 1. Record the provenance

In a private operator notebook, record the Mojum Git commit, Python version, absolute source path, configured `--motor-mode`, `--sensory-mode`, `--temporal-mode`, `--neural-mode`, optional `--room-file`, and the most recent **non-sensitive** checkpoint metadata. Record the exact original CLI options: a different runtime mode or room can be rejected by journal replay. Keep this notebook private.

## 2. Create a transactionally consistent backup

Stop the Aquarium and portable host, then ensure no other process writes to the source. Run the following on a trusted **POSIX** host from a private directory, replacing the two paths. On Windows, use a separately reviewed procedure that sets a restrictive destination ACL before opening the SQLite file; this example fails closed on Windows. The destination must be a **new** file on a trusted filesystem with enough free space. Python's SQLite online-backup API reads a consistent database snapshot; it is safer than copying a live database file.

```python
from contextlib import closing
from pathlib import Path
import os
import sqlite3

source = Path("dante_PRIVATE.sqlite3")
destination = Path("dante_PRIVATE.backup.sqlite3")
if not source.is_file():
    raise SystemExit("source missing")
if os.name != "posix":
    raise SystemExit("this example requires POSIX; use an ACL-hardened Windows procedure")
# O_EXCL prevents overwriting an existing backup; mode 0600 protects it
# from the moment of creation, independent of the process umask.
fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
try:
    # URI mode=ro prevents accidentally creating a missing source database.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"backup integrity check failed: {result}")
except BaseException:
    # The destination may be incomplete: do not label it verified.
    # Preserve it for private diagnosis; never touch the source.
    raise
print("backup created and SQLite structure verified")
```

The code intentionally refuses to overwrite a destination. If it fails, quarantine the partial destination rather than presenting it as a verified backup. Protect backups with restrictive filesystem permissions and your normal encrypted storage policy.

## 3. Check bytes and application replay separately

Calculate a local SHA-256 for the finished backup and record it in the private operator notebook:

```python
import hashlib
from pathlib import Path

path = Path("dante_PRIVATE.backup.sqlite3")
digest = hashlib.sha256()
with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
```

A digest alone does not prove who produced the backup. The database must also pass **Mojum's own full journal replay / chain validation** with the exact recorded runtime selectors. Consult the current checkout's CLI help and journal verification tests for the supported read-only validation route. Do not run a ticking CLI against the only backup: a normal Aquarium launch can append events. If no read-only application verifier is available for this revision, mark the backup **SQLite-verified only; application replay unverified**.

## 4. Restore without destroying the source

1. Stop every writer and identify the exact commit/configuration used to create the journal.
2. Copy the verified backup to a **new** restore path on a private volume; do not overwrite an existing journal or place it under a public web root.
3. Verify the restored file's SHA-256 against the recorded backup digest, and run `PRAGMA integrity_check` using a read-only SQLite connection.
4. Perform the supported **read-only** Mojum event-chain/replay validation with the pinned modes, if available. Fail closed on missing events, manifest mismatch, stale journal head, or corruption. Never silently repair or truncate history.
5. Only after checks pass, designate the restored file as the sole canonical writer target. Keep the original quarantined and read-only until operator sign-off. Do not alternate writes between original and restore.

## 5. Failure drills and limits

- **Crash during backup:** discard/quarantine incomplete destination, keep source, retry with a fresh path.
- **Full disk / permission failure:** stop, inspect free space and permissions, and retry to a fresh destination; never delete the source to make room.
- **Hash mismatch:** reject restore and investigate provenance. Do not infer an attacker solely from a mismatch.
- **SQLite passes, replay fails:** reject application recovery; preserve both files for private diagnosis.
- **Missing runtime flags / room draft:** do not guess; retrieve the original pinned configuration.
- **Host lost:** a backup on the same failed disk is not resilience. Test recovery from an independently stored encrypted copy before claiming a disaster-recovery capability.
- **Concurrent writers / divergent histories:** stop and escalate. This procedure provides no lease fencing, quorum, merge, or two-device failover.

## Acceptance checklist

- [ ] Backup was made to a fresh destination without modifying the source.
- [ ] SQLite integrity check passed on backup and restored copy.
- [ ] Backup digest matches restored copy.
- [ ] Exact journal configuration and source revision were recorded privately.
- [ ] Full Mojum event replay passed **or** its absence was explicitly documented.
- [ ] Restored path remained private, and only one writer was enabled.
- [ ] Recovery from a separately stored encrypted copy was tested before any durability claim.

This runbook is a manual operator aid, not a guarantee of power-cut durability, secure key management, privacy-preserving export, automatic snapshots, or live migration.
