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

For an installed wheel, first create the bundled HTML in a new local file:

```sh
python -m mojum.trace_viewer --output ./aquarium-viewer.html
```

This works without a source checkout and never replaces an existing path.
The command prints the HTML byte count and SHA-256. No trace is included in
the HTML; choose your exported file after opening the viewer.

Open the HTML file directly in a browser and choose the exported JSONL. The
viewer runs without a local web server and keeps the selected file in browser
memory. It checks consecutive ticks, numeric room bounds, the terminal
checkpoint, and the exporter-provided SHA-256 over the exact frame bytes before
enabling playback, seeking, and the telemetry panel.

Both exporter and viewer limit each JSONL line to **64,000 Unicode code
points**. Supplementary characters, such as emoji in valid organism or
acquisition identifiers, count once even though JavaScript stores them as two
UTF-16 code units. The viewer counts long lines with a bounded scan; the
separate **24 MiB UTF-8 file** and **5,000 frame** limits still apply. Integrity
checks continue to hash the exact exported UTF-8 bytes, including newlines.

Modern exports with missing or altered integrity, range, room, or status
metadata fail closed. Truly older traces without the modern proof can only be
opened after selecting the explicit legacy option and are visibly marked
unverified. SHA-256 detects accidental modification; it is not authentication,
encryption, rollback protection, or permission to disclose PRIVATE history.

The viewer has no external scripts or network connections and renders journal
values as text. Its tests cover the current HTML/CSP contract, corrupted and
truncated modern traces, ASCII and Unicode line boundaries, oversized input, and a slower
stale file read losing to the latest file selection:

```sh
PYTHONPATH=src python -m unittest -v tests.test_private_trace_integrity
```

These are offline synthetic-fixture checks, not manual browser/mobile
acceptance and not evidence of a trained brain or a complete digital organism.

## Updating the bundled viewer

`examples/aquarium_trace_viewer.html` remains the canonical source. When editing
it, copy the same bytes to `src/mojum/data/aquarium_trace_viewer.html` so the
wheel carries the same application. `tests/test_trace_viewer_package.py` and
the installed-wheel smoke enforce exact byte equality.
