# PRIVATE Aquarium trace segments R0.1

**Status:** experimental opt-in, stacked on offline real-journal exporter
[PR #74](https://github.com/Garyu-tt/mojum/pull/74). This is a
review/QA slice, not a live streamer, automatic history-sharing service,
continuous Android background runtime or authoritative distributed replica.

The exporter in #74 previously exported all frames or refused when the
viewer limit of 5000 was exceeded. For a long-running local synthetic
organism, this meant an otherwise valid history could no longer be inspected
in the browser. This addition selects an **explicitly bounded time range**
*after* taking a consistent SQLite backup and replay-verifying the **entire
captured Noosphere journal**; it never trusts or verifies only the selected
few frames.

## CLI: export one private segment

First create a real local synthetic Aquarium (see #74 README). With the
full original journal at ./dante.sqlite3, run:

    PYTHONPATH=src python -m mojum.aquarium_trace_export \
      --db ./dante.sqlite3 \
      --output ./dante-0000-0100.private.jsonl \
      --allow-private-export \
      --start-tick 0 --frame-limit 100

For the next 100 frames:

    PYTHONPATH=src python -m mojum.aquarium_trace_export \
      --db ./dante.sqlite3 \
      --output ./dante-0100-0200.private.jsonl \
      --allow-private-export \
      --start-tick 100 --frame-limit 100

The output metadata contains frames, first_tick, next_tick,
source_total_ticks, and has_more. Each segment also ends with a small
metadata footer carrying the exact same range. The viewer ignores footer
rows and displays segment frame ticks as recorded; opening a segment from
tick 100 does not falsely display it as tick 0.

When has_more is true, use next_tick as the subsequent --start-tick.
A final segment may have fewer than --frame-limit frames and then
has_more is false.

With *neither* range option, the old export-all-or-fail behavior remains.
An empty future range, one missing range option, a boolean/noninteger tick,
or a nonpositive or >5000 --frame-limit is rejected without creating a
target artifact. **No implicit truncation**.

## Integrity, privacy and failure semantics

- SQLite source is opened read-only and snapshotted through the native
  backup API including uncheckpointed WAL pages. The original source
  database is never reopened with writable Aquarium initialization.
- The staged snapshot is **fully** replay-verified against its bootstrap,
  all historical frames and model state, and optionally an **independently**
  held expected journal head. The selected segment is only a projection
  of that already validated history.
- The output remains plaintext **PRIVATE** and should stay on trusted
  local storage. Explicit --allow-private-export remains mandatory,
  output permissions are 0600 on POSIX, filename must not already exist,
  and temporary output files are removed on errors.
- A segment's footer journal head refers to the **entire source snapshot**,
  not a cryptographic identity for the selected text. The metadata also
  includes SHA-256 of the exported bytes; without a trusted separately
  held expected hash/head this is not authentication.
- Concurrent source writes are allowed: each export is a single
  consistent point-in-time SQLite snapshot. Separately exported segments
  may have *different* full-journal heads if the organism continues running.
  This is normal, but they are not automatically authenticated into one
  common snapshot. For a strict forensic comparison, stop writes or pin the
  expected full journal head per export.
- Physical motor control, neural consciousness, 3D simulation and
  distributed consensus are explicitly out of scope.

## Tests

    PYTHONPATH=src python -m unittest discover -s tests \
      -p test_trace_segment_export.py -v
    PYTHONPATH=src python -m unittest discover -s tests -v

Seven new tests cover real SQLite middle-range and tail projections,
metadata and source byte invariance, default full-history compatibility,
invalid/unpaired/empty ranges, nonoverlapping segments composing into
one consecutive viewer trace, and CLI output disclosing metadata only.

This branch is dependent on #74; do not merge it to main before its parent
has passed independent review. Existing #55 and #51 release criteria remain
separate and open.
