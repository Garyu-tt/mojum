# Mojum R0.1: HMAC-protected optional replay cache (CPU-first)

**Experimental draft — not a second authoritative journal or encrypted backup.**

This is stacked on PR #98. The canonical Noosphere SQLite history remains
the only durable source of committed synthetic experience. The snapshot is
an explicit, PRIVATE **plaintext** acceleration cache, authenticated by
a separately provisioned owner-only 32-byte HMAC-SHA256 key. It does not
encrypt the genome, learned policy, synthetic state or observations.

## Startup and restart security

1. Before any SQLite bootstrap, validate the separately stored key and
   HMAC snapshot. A missing snapshot is allowed for the first run.
   An existing invalid snapshot is **never silently ignored**.
2. On snapshot creation, check current SQLite committed head, count and
   **full hash chain**. Store the exact canonical Aquarium checkpoint,
   GENESIS ledger, world revision/position, shadow heartbeat learner,
   goal-free motor evidence and numeric toy-contact learner. Write a new
   0600 file with fsync and atomic rename; keep prior sidecar safe on crash.
3. On restart, verify the **entire SQLite journal hash chain** and optional
   independent expected head. Ensure bootstrap, agent/manifest, saved
   prefix sequence/hash, tick and all component schemas match. Validate
   reconstructed state with the original per-component validators.
4. The signed prefix is **not simulated again**; only events committed
   after it are processed through the canonical deterministic Aquarium.
   Refuse changed count, stale head or any divergent tail, and preserve CAS
   for future appends.

**Performance:** simulation cost falls when a good snapshot is recent;
full chain hashing and database reads are still O(number of events).
The hard #98 cap remains 100,000 events, not infinity. The cache is
not encrypted, not an attestation and not a safe remote backup.
An attacker possessing both the SQLite and HMAC key can forge a new
compatible snapshot. A previously signed snapshot plus old SQLite can
be rolled back together unless a latest trusted head is pinned elsewhere.

## CPU or owner-operated Android/Termux commands

From this PR branch in a directory private to the current user:

    mkdir -p "$HOME/mojum-private"
    chmod 700 "$HOME/mojum-private"
    PYTHONPATH=src python -m mojum.verified_snapshot \
      --create-key "$HOME/mojum-private/replay.hmac.key"

Provisioning is a **separate one-time operation**; an existing key is
never silently overwritten. Key bytes are never printed. On later
restarts use the **same** key file and snapshot path:

    PYTHONPATH=src python -m mojum.portable_host \
      --db "$HOME/mojum-private/dante.sqlite3" \
      --snapshot-file "$HOME/mojum-private/replay.snapshot.json" \
      --snapshot-key-file "$HOME/mojum-private/replay.hmac.key" \
      --ticks 120 --interval-ms 1000

Re-run the host to resume. It prints metadata, not PRIVATE frames or
key contents. A crash after SQLite append but before snapshot publication
leaves an older signed cache; the committed tail will be replayed.
If the key is lost or cache invalid, a user may explicitly move aside
the cache and do a full journal replay; the program never downgrades
without deliberate action.

## Test and acceptance

    PYTHONPATH=src python -m unittest discover -s tests \
      -p test_verified_replay_snapshot.py -v
    PYTHONPATH=src python -m unittest discover -s tests -v

Tests use an actual synthetic R0.13 SQLite journal, check exact parity
of full vs cached reconstruction, fast tail-only simulation, HMAC
tampering and validly authenticated invalid state, prefix corruption,
stale rollback, 0600 permissions, symlink rejection, no-clobber key
creation and two live independent portable-host CLI subprocesses.

No physical Android/Pixel 9 execution, native APK, Doze/background
survival, energy or memory benchmarks, encrypted memory, distributed
consensus, real neural cognition, physical touch or sensor access was
implemented or verified. PR #51 / #55 remain blocked. Keep draft and
require an independent security/code review before merging.
