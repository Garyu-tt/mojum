# Mojum R1.0 QA — SQLite WAL and real SIGKILL crash matrix

This is a **single-host CPU-first regression layer** for the opt-in
durable-intent writer developed through PR #94. It does not integrate a
distributed leader, enable new external APIs, modify production hosts or
claim that software-injected process termination equals a hardware power
cut.

The protected PRIVATE Noosphere journal and LocalAuthority registry use
**two separate SQLite databases**. Each has its own transaction boundary.
No amount of `synchronous=FULL` converts their two independent commits
into one all-or-nothing distributed transaction.

## 1. Enforce the actual connection's WAL durability mode

`IntentGuardedAquarium` now checks both its **live writer connections**:

| SQLite PRAGMA | Required value |
| --- | --- |
| `journal_mode` | `wal` |
| `synchronous` | `FULL` (numeric 2) |

Validation runs on construction, before preparing every event and before
finalizing a prepared event. A weakened connection such as
`PRAGMA synchronous=NORMAL` or `OFF` fails closed; before a new append
it leaves the ACTIVE registry unchanged, and after the journal append it
leaves the prepared authority FROZEN for independent review.

**Why inspect live connections?** `journal_mode` is stored in SQLite
metadata, but `synchronous` is a **per-connection setting**. Inspecting
a new read-only connection would tell us its own setting, not whether the
writer actually committed with `FULL`. These checks do not attest
hardware write caches, filesystem flush integrity, device firmware,
filesystem barriers, disk replacement or malicious root/service-UID
processes.

The legacy non-opt-in writer path is unchanged.

## 2. External Linux SIGKILL boundaries

A separate worker Python process executes a real synthetic Aquarium
operation. It signals a deterministic barrier on a private stdout pipe,
and the parent process sends **external `SIGKILL`**. The killed process
cannot call normal Python cleanup, SQLite connection `close()`, or a
graceful WAL checkpoint.

| Barrier at termination | Expected surviving state |
| --- | --- |
| Committed prepare, before event | FROZEN intent, no extra event |
| **Inside uncommitted Noosphere SQLite INSERT transaction** | FROZEN intent; partial INSERT rolls back |
| Committed Noosphere event, before authority finalize | FROZEN intent; exact extra event requires review |
| **Inside uncommitted authority finalize transaction** | FROZEN intent and committed extra event; authority UPDATE and intent DELETE both roll back |
| After both final commits | ACTIVE new generation; old generation refused |

After killing each worker, tests open the actual databases afresh,
run `PRAGMA quick_check`, replay and verify the entire real Noosphere
hash chain, compare event count and tick, and run the existing
`inspect_prepared` forensic audit. No pending state is auto-unfrozen,
auto-promoted or silently accepted.

This covers WAL transaction recovery and OS process death. It does
**not** prove resilience against the computer actually losing power,
filesystem journal corruption, storage devices that lie about `fsync`,
or repeated unclean shutdowns during hardware-level WAL checkpoints.

## 3. Raw SQLite file copy is not a safe cold backup

The regression pins an old SQLite reader transaction while another
writer appends a real Noosphere event into WAL. The test copies only
the primary `*.sqlite3` file: this old copy is missing the new event.
A proper `sqlite3.Connection.backup()` taken while WAL is active
contains the new committed event and its correct head.

The source registry has not authorized the out-of-band append, so the
prepared-intent inspector returns
`UNANCHORED_JOURNAL_WITHOUT_INTENT_STOP`, never automatic acceptance.

Do not deploy `cp /var/lib/.../*.sqlite3` as a Noosphere backup:
a checkpoint or coordinated SQLite Backup API is required, and
the registry must be included in a separately reviewed *coherent*
handoff procedure. Merely backing up each database at independent
times also fails to provide an atomic journal/authority snapshot.

## How to run on a disposable development machine

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_sqlite_wal_sigkill_r01.py' -v

PYTHONPATH=src python -m unittest discover -s tests -v
```

The SIGKILL suite is enabled only on Linux. All tests create fresh
synthetic journals under Python `TemporaryDirectory`, never contact
external services, never use production data and do not need root.
The ordinary CI runs them on Python 3.11, 3.12 and 3.13. Real
separate-UID acceptance remains covered by the pre-existing Linux
ephemeral-root workflow from PR #85/#94.

**Next gate before real hardware use:** independently review the
full stacked PR chain; run a host-specific WAL/ACL/backup acceptance on
a **nonproduction storage device** with confirmed hard power interruption
and checksums, then design privileged operator-only reconciliation.
No confidence claim about hardware durability should be inferred
from successful SIGKILL tests alone.
