# Offline Aquarium trace viewer

`examples/aquarium_trace_viewer.html` is a local observer for an already
committed synthetic Aquarium run. It does not run the Aquarium, open SQLite,
contact a server, or claim a brain, awareness, or physical actuation.

## Export a private trace

Create or resume a local journal, then explicitly opt in to exporting its
PRIVATE frames:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante.sqlite3 --ticks 120
PYTHONPATH=src python -m mojum.aquarium_trace_export \
  --db ./dante.sqlite3 \
  --output ./dante.private.jsonl \
  --allow-private-export
```

Use the same mode flags that created the journal. For example, a developmental
contact trace uses `--motor-mode developmental_fixture --sensory-mode toy_probe`.
The exporter verifies a temporary SQLite copy, writes a new file without
overwriting an existing destination, and prints metadata only. Keep both the
SQLite journal and JSONL file private; the JSONL is plaintext.

For an independently recorded chain head, add `--expected-head SHA256`. To
export a bounded segment, pass `--start-tick` and `--frame-limit` together.
Those options still replay and verify the complete source journal before
publishing the selected PRIVATE frames.

## Open and verify

Open the HTML file directly in a browser and choose the exported JSONL. The
viewer runs without a local web server and keeps the selected file in browser
memory. It checks consecutive ticks, numeric room bounds, the terminal
checkpoint, and the exporter-provided SHA-256 over the exact frame bytes before
enabling playback, seeking, and the telemetry panel.

Modern exports with missing or altered integrity, range, room, or status
metadata fail closed. Truly older traces without the modern proof can only be
opened after selecting the explicit legacy option and are visibly marked
unverified. SHA-256 detects accidental modification; it is not authentication,
encryption, rollback protection, or permission to disclose PRIVATE history.

The viewer has no external scripts or network connections and renders journal
values as text. Its tests cover the current HTML/CSP contract, corrupted and
truncated modern traces, legacy boundaries, oversized input, and a slower
stale file read losing to the latest file selection:

```sh
PYTHONPATH=src python -m unittest -v tests.test_private_trace_integrity
```

Navigation and timer behavior has a separate deterministic test using three
frames exported from an actual synthetic SQLite Aquarium journal:

```sh
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_offline_viewer_navigation_qa.py' -v
```

It drives the unmodified viewer in a Node VM with virtual interval callbacks
and verifies playback/reset state. It does not replace the real Chromium smoke.

These are offline synthetic-fixture checks, not manual browser/mobile
acceptance and not evidence of a trained brain or a complete digital organism.
