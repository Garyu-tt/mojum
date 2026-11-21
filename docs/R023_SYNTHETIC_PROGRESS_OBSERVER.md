# R0.23 — synthetic Aquarium progress histogram from an immutable HMAC-pinned history

Experimental **Draft** stacked on [R0.22 PR #167](https://github.com/Garyu-tt/mojum/pull/167).
A local synthetic Aquarium is a deliberate CPU-only fixture, not a
human-level brain, digital consciousness or subjective life evidence.
No production data, active user agent, connected VM, real sensor/device,
cloud transfer, `main`, or real SECRET is accessed by this implementation.

## The user-visible requirement

The observer should eventually answer:

- How many committed virtual motor frames have actually occurred?
- How many synthetic movements collided with known boundaries?
- How many independent toy probes were actually requested, how many
  **measured** contact, and how many updated the local numeric
  sensory association table?
- Which explicit epoch boundary was durably crossed?
- How did those **recorded** measurements accumulate over virtual time,
  rather than only showing one last checkpoint value?

R0.23 provides bounded numeric and deterministic **16-bin default**
histograms. No UI or phone screen is claimed to exist yet. A future
Aquarium interface can plot these values as read-only history.

## Privacy and trust

`summarize_synthetic_progress(observer, include_private_metrics=True, bins=16)`
requires an exact R0.22 `QuarantinedObserver` already authenticated against
its separately stored owner HMAC key, independently pinned receipt SHA-256
and the reconstructed R0.21 quarantined SQLite. The observer uses
`mode=ro&immutable=1` and `PRAGMA query_only=ON` and checks the original
history's SHA-chain and signed custody record before and after deriving
any metrics.

**Default without an explicit flag** returns only redacted R0.22
source metadata. No private event iteration happens in the new metrics
layer when `include_private_metrics=False`. The CLI similarly stays
private by default. Private per-frame payloads are examined **in-process
only after explicit opt-in**, never exported directly or published
to GitHub Actions artifacts.

With opt-in, returned data contains only:

- exact counted motor frames, bootstrap event, explicit epoch marker,
  bound-collision frame count, requested synthetic probes, measured
  contacts and numeric association updates;
- a fixed-size 1–32 bucket histogram with those same counters;
- owner-verified original SHA head, agent ID, event count and
  `QUARANTINED_READ_ONLY` state.

**No** raw private event objects, toy IDs, positions, RGB colors,
learned association values, genomes, memory episodes, model weights,
timestamps, learned policy, private key material or filepaths appear
in the report. This histogram describes **activity**, not grades,
knowledge mastery, psychological welfare, biological development,
sentience or valid intelligence improvement. Such labels require
independent task-specific assessments and much stronger evidence.

The algorithm streams a finite event history in bounded batches,
using only 1–32 accumulator buckets in memory. The frame tick counter
must be contiguous from 0; the bootstrap appears exactly at the
beginning, and explicit epoch marker indices must be monotonic and
occur at the committed frame boundary. Unexpected world semantics,
missing boolean measurements or a non-Aquarium history fail closed,
rather than being silently classified as evidence of agent development.

## Offline CLI

```bash
# Redacted by default, no access to PRIVATE progress metrics:
PYTHONPATH=src python -m mojum.segment_archive_cli observe-replica \
  --db /owner-private/RECOVERED-QUARANTINED.sqlite3 \
  --receipt-file /owner-private/receipts/source.json \
  --key-file /owner-private/secrets/key-32bytes.bin \
  --expected-receipt-sha OUT_OF_BAND_PINNED_SHA256 \
  --expected-agent-id dante-fixture-001

# Explicitly allow aggregated synthetic progress, 8 virtual-time bins:
PYTHONPATH=src python -m mojum.segment_archive_cli observe-replica \
  --db /owner-private/RECOVERED-QUARANTINED.sqlite3 \
  --receipt-file /owner-private/receipts/source.json \
  --key-file /owner-private/secrets/key-32bytes.bin \
  --expected-receipt-sha OUT_OF_BAND_PINNED_SHA256 \
  --expected-agent-id dante-fixture-001 \
  --include-private-progress --progress-bins 8
```

Combining `--include-private-metadata` and
`--include-private-progress` is denied: the user must deliberately
choose one disclosure mode. Range outside 1–32 bins is rejected.

## Test gates

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_progress_observer.py -v
```

Three Python versions validate a real synthetic `Aquarium` for
130 motor frames plus one durable bootstrap frame, a complete
128-sealed + 3-live-tail physical history, HMAC owner receipt
and quarantined recovery. The redacted-by-default call never
iterates PRIVATE events (mock guard); opt-in histograms total
130 frames; world collisions and toy sensor association update
counts match the source's own deterministic checkpoint. Tests also
deny malformed bin lengths and non-Aquarium history, and exercise
the real CLI without PRIVATE stdout leaks.

A separate Python 3.13 stress job:
- runs 10,050 *actual* virtual motor frames, with a durable explicit
  epoch crossing at tick 9999 and more than 4,000 measured toy
  contact updates, producing exactly 10,052 journal events;
- HMAC signs the complete sealed+live-tail physical archive;
- makes original synthetic SQLite unavailable and restores into a
  quarantined, immutable SQLite;
- verifies the resulting 16 histogram bins total exactly 10,050
  frames, one epoch marker, and the exact source-replayed numeric
  association-update count (without exposing raw measurements);
- verifies the observation produces neither WAL nor SHM files.

## Remaining architecture goals

Read-only UI plots and educational scores are separate features.
Real developmental progress needs explicit lessons, controlled
tasks, retention curves, transfer tests and validated scores:
**activity counts are not sufficient**. No automatic writer
promotion, remote backup, trusted second independent physical host,
authenticated encrypted synchronization or neural suffering claims.
[Issue #141](https://github.com/Garyu-tt/mojum/issues/141) remains
open for independent failure-domain custody.

Do not merge or claim tests green until the final SHA has completed
the exact Actions matrix and independent code review.
