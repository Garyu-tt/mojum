# Litestream 0.5.18 local cold-archive spike (issue #693)

**Status:** Draft / opt-in, PUBLIC synthetic fixtures only. This is an
independent Litestream slice; Borg work is in PR #729. The existing
`mojum.aquarium_cold_copy` and canonical SQLite/GENESIS/Noosphere journal are
not modified. No service is started automatically, no dependency added to the
Python package, and there is no auto-migration, network replica, or failover.

## Why this is intentionally *not* a PRIVATE backup yet

Litestream 0.5.x's simple `file://` replica is **unencrypted**. Unlike the
v0.3.x Age facility, v0.5.x has no built-in Age encryption in this mode.
An owner-only directory is access control, **not encryption**. This adapter
**requires `public_synthetic=True`** and must never be used for real
PRIVATE organism journals, sensitive memories, secrets or production storage.
The caller is responsible for ensuring all input events are genuinely
public, disposable synthetic data. The flag is a deliberate opt-in, not a
cryptographic content classifier.

The separately developed Borg path may eventually provide properly reviewed
encrypted archives, but no combined security guarantees are claimed. Real
PRIVATE use needs verified authenticated encryption/key management, independent
offsite copies, credential policy, signed anchors, recovery drills and consent.

## Upstream/version and licensing

- [Litestream upstream](https://github.com/benbjohnson/litestream):
  Apache-2.0, pin **v0.5.18**, released 2026-11-03; never silently use
  another CLI. The project is not vendored.
- [CLI replication](https://litestream.io/reference/replicate/):
  `replicate -once -force-snapshot DB_PATH file:///ABS/PATH` (flag order matters).
- [CLI restoration](https://litestream.io/reference/restore/):
  `restore -integrity-check full -o NEW_PATH file:///ABS/PATH`.
- [Local file replica](https://litestream.io/guides/file/):
  stores snapshots/LTX segments, not a plain SQLite copy.
- Linux/macOS are supported upstream; this experimental adapter requires
  POSIX. Windows is explicitly out of scope. No cloud scheme, proxy, HTTP,
  S3, NATS or Litestream config file is passed to the subprocess.

Only an **independently SHA-256-pinned executable file** is allowed; both
executable bytes and printed version must match. Do not confuse a binary SHA
with the archive release tarball SHA.

## Minimal usage (Python 3.11+)

Use a fresh private (0700) directory such as a `tempfile.TemporaryDirectory()`
and an explicitly downloaded, independently verified local Litestream binary.
No automatic download or install occurs.

```python
from pathlib import Path
from mojum.litestream_cold_archive import create_local_archive, restore_local_archive

# seed and graph MUST be the same pinned fixture inputs as the source Aquarium.
# source.sqlite3 must already contain a committed PUBLIC synthetic Aquarium run.
# Binary SHA is the real 64-hex SHA-256 of the locally installed executable.
archive = create_local_archive(
    "source.sqlite3", "/private-work/new-litestream-replica", seed, graph,
    binary=Path("/trusted/bin/litestream"),
    binary_sha256="<independently verified executable SHA-256>",
    public_synthetic=True,
)
# SAVE the returned anchor securely and OUTSIDE the backup. It contains only
# owner, journal head, restored file SHA, virtual tick and event count.
restored = restore_local_archive(
    "/private-work/new-litestream-replica", "/private-work/new-restored.sqlite3",
    seed, graph, binary=Path("/trusted/bin/litestream"),
    binary_sha256="<independently verified executable SHA-256>",
    expected_agent_id=archive["agent_id"],
    expected_journal_head=archive["journal_head"],
    expected_sha256=archive["restored_sha256"],
    expected_tick=archive["tick"],
    public_synthetic=True,
)
```

The example directories and values are placeholders. **Never** commit any
actual SQLite journal, replica bytes, signing key or personal data to Git.

## What actually happens

1. Require explicit PUBLIC synthetic opt-in, safe private destination parent,
   brand-new replica path, pinned regular executable and exact CLI version.
2. Use existing `export_cold_copy`: readonly online SQLite backup including
   committed WAL pages, full replay of a temporary copy, SHA-256 and journal
   head captured. Litestream does **not** open the canonical/source journal.
3. Under a private temporary directory, invoke **one-shot forced snapshot**
   into a brand-new `file://` replica; enforce 256 MB / 128 file output budget,
   no symlinks or public-readable modes. Runs are limited to 1–180s per command.
4. Restore once to a disposable location and require full `PRAGMA integrity_check`
   via Litestream plus complete `verify_cold_copy` replay of the expected
   organism identity, virtual tick and journal head.
5. Return an external anchor: `restored_sha256`, head, tick, identity.
   The separate restore API refuses to publish if that independently pinned
   anchor fails, archive is malformed, or destination/sidecars already exist.
   It hard-links the verified file into a **new** path, mode 0600.
6. A failed initial backup removes its incomplete newly created replica;
   a failed restore leaves the original journal and output path untouched.
   No logs, events or subprocess stderr are returned.

Resource budgets are guards, **not** benchmark data: maximum SQLite file
128 MB (existing cold-copy bound), total local replica 256 MB and 128 regular
files, 180 seconds per subprocess. Actual CPU/RSS, sync time and RPO/RTO
are **unmeasured** without the pinned real executable.

## Tests / independent acceptance

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_litestream_cold_archive.py' -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_aquarium_cold_copy.py' -v
```

The first suite uses a **mock Litestream executable** and an actual synthetic
Aquarium SQLite WAL source: it tests command ordering, no mutation of the live
source, digest/version pin, no-clobber restore, corrupted replica, failed
subprocess, unsafe permissions and regression replay. Mocked CLI parity is
not proof of Litestream's native LTX semantics.

A separate, controlled native smoke on Litestream v0.5.18 is required to prove
that the CLI creates restorable LTX files with this contract, then run the
exact-HEAD repo CI and independent QA before merging. A second-drive/disaster
test and objectively measured RPO/RTO are still outstanding.

## Explicit exclusions

Not continuous streaming; not a coordinator/lease/fencing protocol; not two
physical fault domains; not credentialed replication; not active-active
SQLite; not an encrypted archive; not a trusted identity signature; not
a real full organism backup. A stale or substituted replica might pass if the
external anchor itself is compromised. Any failover requires a separate single
writer/fencing decision; **never run two writable replicas concurrently**.
