# Real Aquarium trace viewer R0.1 — offline-only recorded playback

The single HTML file examples/aquarium_trace_viewer.html is an **observer**
for already committed/saved synthetic Aquarium frames, not a second animated
creature, a remote web server, or a brain. It does not execute the Aquarium
runtime, make any network request, import SQLite directly, or show fictional
thoughts. The file remains in local browser memory; no localStorage or
IndexedDB is used.

## Create the source Aquarium on plain main (no R0.13 dependency)

The trace exporter in this PR reads real committed events from the **already
existing** CPU-only canonical Aquarium SQLite database. First, run the
unchanged default scripted fixture (from the repository root):

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante.sqlite3 --ticks 120
    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante.sqlite3 --ticks 30

Then export the **entire replay-verified history** into a new local file
with an explicit PRIVATE-export acknowledgement:

    PYTHONPATH=src python -m mojum.aquarium_trace_export \
      --db ./dante.sqlite3 \
      --output ./dante.private.jsonl \
      --allow-private-export

The source SQLite is opened in read-only mode; a SQLite Backup API snapshot
is verified by deterministic Aquarium replay **in private scratch space**.
The original journal is not bootstrapped, written or altered by the exporter.
The output is created with restrictive mode 0600 on POSIX, cannot overwrite
an existing destination, and is at most 24 MiB/5000 frames. CLI stdout
contains metadata only, **never PRIVATE frames**. An optional --expected-head
argument pins the captured snapshot against a separately stored journal head.
Without an independent anchor, a self-consistent malicious rewrite cannot be
authenticated. Backup staging can read committed WAL pages while the original
Aquarium is open; no multi-device migration or continuous live-feed claim.

## Optional live-produced JSONL on unmerged R0.13 branch

The [PR #65](https://github.com/Garyu-tt/mojum/pull/65) feature branch
also offers --watch, which emits real goal-free movement and toy-contact
frames as consecutive newline-delimited JSON. That mode is **not on main**:

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante_toy.sqlite3 --ticks 120 \
      --exploration-mode goal_free --sensory-mode toy_probe \
      --watch > ./dante_toy.private.jsonl

Do not expose either PRIVATE file publicly. On the main-based branch,
the read-only exporter is suitable for examining existing recorded
scripted Aquarium history without requiring #65. Once a later review
integrates #65 / RC #75, this viewer can open the richer --watch files too.

Open examples/aquarium_trace_viewer.html directly in a browser with a
file:// URL, then select the JSONL trace using the file picker. There is
no need to run an HTTP server or install browser extensions. Use playback,
timeline slider, per-frame controls and the numeric telemetry side panel.
The canvas plots the real recorded world.position coordinates and blocked
outcomes in the standard **4×4 m synthetic room**; it does **not** guess
where hidden obstacles or hidden toy truth are located.

A single frame may contain:

- A tick/time and the actual synthetic heartbeat/interoception signal.
- The saved developmental.intent action and measured blocked/allowed result.
- The R0.14 developmental.observation.range_bucket_5cm_ewns four-ray signal,
  when that separately stacked branch is the source (not yet combined with
  toy-probe R0.13 in the official repo).
- The R0.13 toy-probe near-field RGB/range cue, requested synthetic probe,
  actual measured_deflection (when contact occurred), and the previous
  predicted deflection.
- Explicit brain.status=UNIMPLEMENTED when the placeholder brain was used.
  This is not an invented inference about awareness or emotion.

## Boundaries

- **The browser itself does not open SQLite.** The new Python exporter
  reads and verifies a real source SQLite snapshot into a PRIVATE JSONL
  file; the HTML then reads only that local JSONL file. Original main
  still lacks the R0.13 live --watch producer.
- It skips standalone summary lines of the known checkpoint schema.
  Frame ticks must be consecutive; repeated or missing ticks are rejected.
- Input is limited to **24 MiB**, **5000** frames and **64k characters per
  input line**. Corrupt, wrong-schema, malformed numeric-position/heartbeat,
  or out-of-room data are rejected without running payload code.
- CSP disables network connections, external frames, media, fonts and images.
  No external JS, CSS, code eval or HTML injection of journal values is used;
  user-supplied text is always set through textContent.
- **No authentication, signature or chain verification** is performed on
  JSONL. Someone who controls a local file can fabricate a trace. For
  cryptographic integrity use separately pinned source-journal verification.
- The player is a **recording**, not a live feed. It is not browser-based
  simulation, physical actuator telemetry, or proof of neural autonomy.
- Nothing here establishes Android/PWA installation, background service,
  network replication, MuJoCo physics or a cognitive digital organism.

## QA

    PYTHONPATH=src python -m unittest discover -s tests \
      -p test_offline_trace_viewer.py -v
    PYTHONPATH=src python -m unittest discover -s tests -v

The test's standard-library HTML parser asserts local-only/CSP structure.
When Node is available, its built-in vm module executes the actual inline
viewer JavaScript against a minimal fake DOM and synthetic PRIVATE JSONL
fixture, including real UI state transitions, numeric ray rendering, saved
probe telemetry, HTML-looking values displayed literally and malformed or
oversized trace refusal. This is stronger than static JS syntax checking,
but **not a manual desktop/mobile browser rendering acceptance**; that
still needs an independent smoke run when UI is published.

GitHub PR review remains required; do not merge just because CI passed.
