# Mojum streaming Noosphere replay R0.1 — 10k -> 100k-event ceiling

**Experimental CPU-first bounded-memory implementation. Not an indefinite
digital organism, a fast/signed snapshot, encryption or full background
Android service.** Stacked on the portable CPython/Termux trial #97;
parent and main were not edited.

## Why the previous host stopped

Before this PR, an Aquarium reopening its PRIVATE SQLite Noosphere first
performed a full chain verification, then called
\`store.dataset(include_nonpublic=True, limit=MAX_REPLAY_EVENTS)\` to
materialize up to 10,000 rows into a giant JSON/Python list for replay.
\`dataset()\` intentionally refuses limit >10,000 and copies the entire
projection, so raising only the Aquarium constant would have broken
restart and private trace export.

Every \`Aquarium.step()\` also executed \`SELECT COUNT(*)\` through
\`store.count()\` to enforce the append budget, even though the current
writer has a pinned last committed journal head and a CAS append.

## Changes

- \`LocalNoosphere.iter_events(include_nonpublic=False, batch_size=128)\`
  exposes a read-only, ordered SQLite \`fetchmany()\` cursor (bounded 1..1024
  rows per fetch). PRIVATE/RESTRICTED are hidden unless explicitly opted
  in; \`dataset()\` retains all previous filters, result schema and its
  original independent 10,000-row *projection* limit.
- Canonical \`Aquarium._replay()\` still **verifies the full hash chain**
  (and optional externally known expected journal head), then walks every
  verified row in order through the same deterministic simulation, genetic
  acquisitions, motor policy, toy contact and learner adoption. It does
  **not** trust database-stored model snapshots, skip early events or
  silently truncate an old private history.
- Streaming replay checks contiguous database sequences, record count,
  last event hash and actual SQLite head once more before adopting the
  reconstructed RAM state. If a writer mutates the history during the
  scan, replay refuses the stale/incomplete snapshot.
- After successful bootstrap/replay \`Aquarium\` retains the committed
  event count and increments it only following a successful SQLite CAS
  append, so each tick avoids repeating a global \`COUNT(*)\` scan.
- The explicit **hard total-event ceiling becomes 100,000** (including
  bootstrap and acquired-gene events), still checked before appending
  each event. It is a deliberate resource guard, not a theoretical
  lifetime guarantee. The next stage should introduce a separately
  verified checkpoint format plus an incremental snapshot tail instead
  of pretending 100k is infinity.
- The read-only PRIVATE JSONL exporter also streams the entire staged
  database through this cursor, selects only explicitly requested
  bounded windows of up to 5,000 frames and remains protected by its
  existing 128MB SQLite source, 24MiB output, 64k-character line,
  no-clobber 0600 and SHA-256 WebCrypto viewer rules.
  \`--start-tick\` now accepts 0..99999; range outside source history
  still errors instead of fabricating missing frames.
- The portable foreground host updates its rejection message to
  report the new limit dynamically and maintains its smaller bound of
  1..1000 ticks **per invocation**, wall pacing and Ctrl-C/SIGTERM
  checkpoint-on-stop semantics.

## Local reproducibility

Full stdlib regression on Python 3.11+:

    PYTHONPATH=src python -m unittest discover -s tests \
      -p test_streaming_journal_replay.py -v
    PYTHONPATH=src python -m unittest discover -s tests -v

The original port/Termux flow remains:

    PYTHONPATH=src python -m mojum.portable_host \
      --db "$HOME/mojum-private/dante.sqlite3" \
      --ticks 120 --interval-ms 1000

Each run must use the **same** private SQLite file. On reopened runs,
both the full checksum chain and actual canonical synthetic experience
are replayed, then new ticks append from the same pinned head.

**Actual old-boundary stress:** run this explicitly (not part of routine
three-version unittest discovery):

    python tests/stress_streaming_journal_boundary.py

A separate single-Python-3.13 GitHub Actions job starts from a real
fixture-only SQLite Aquarium, commits **10,002 frames** (10,003
events), closes and reopens the database, replays every event, appends
one more frame and exports ticks **10000..10002** from the fully
verified 10,004-event source. It verifies expected contiguous ticks,
checkpoint head, footer SHA metadata and one complete private JSONL
segment. No real user data, real phone, external service or network
upload is used. The job prints aggregate timings and counts, not frame
payloads or secret private history. The exact-run result must be
checked before claiming this boundary stress passed.

## Explicit remaining risks

- **Replay CPU time still grows linearly** with event count because full
  deterministic simulation and chain hashing are redone on reopening.
  The memory footprint is bounded by a row page and the current
  simulated state; the *full journal* is still durable on disk and
  grows linearly with time. The exporter itself creates a temporary
  SQLite snapshot and bounded JSONL output.
- MAX_REPLAY_EVENTS=100000 is not a lease, time-based quota or
  guarantee that devices with tiny RAM/disk can hold 100k events.
  Disk-space and battery budgets require measured physical-device
  profiling; long-term viability needs secure incremental snapshots.
- A single staged SQLite source is **not** distributed consensus.
  Changes in the replay implementation or fixture schemas can
  intentionally reject an incompatible historical journal.
- Hash-chain and trace-file SHA-256 are checksums, **not authenticated
  provenance** unless their expected values are pinned independently.
- No physical Android/Termux environment, cross-device replication,
  real camera/body/brain or trusted periodic wake-up was tested here.
  Release issue #51 and P0 #55 stay open. Keep all PRs Draft until
  independent review and explicit owner merge approval.
