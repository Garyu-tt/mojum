# Mojum portable CPU-only host R0.1 — local Android/Termux trial

**Status: experimental foreground CPython host, not an APK, daemon,
Android background service, neural organism or real phone acceptance.**

This integrates with the **canonical, replay-verified, synthetic** R0.13
Aquarium from the review branch #95. It does not duplicate a brain or
copy memory into another process. All steps append to the **same** PRIVATE
SQLite Noosphere journal and the next invocation reopens, validates and
replays it. One ordinary CPU is sufficient; no NVIDIA/CUDA/GPU/LLM
downloads, cloud account or network listener is required.

The command is bounded to 1..1000 ticks per invocation, pacing at
20..3,600,000 ms between commits. The default is 120 ticks at 1000 ms.
An interval controls **wall-clock delay only**. Aquarium's immutable
virtual simulation step remains 10,000,000 ns (10 ms) per tick, whether
the host runs at 1 Hz or 50 Hz. Do not confuse real-time scheduling
with virtual time advancement.

## Linux / desktop local smoke

From the source checkout of this branch:

    mkdir -p "$HOME/mojum-private"
    chmod 700 "$HOME/mojum-private"

    PYTHONPATH=src python -m mojum.portable_host \
      --db "$HOME/mojum-private/dante.sqlite3" \
      --ticks 20 --interval-ms 50

Run again with the *same* path; it will begin with tick 20, not overwrite
the journal or create a second organism. The final stdout JSON contains
**metadata only**: starting/ending tick, committed count, journal head,
elapsed wall seconds and reason for stopping. The private genome,
probe measurements, episodes and predictor state are not printed.

A SIGINT (Ctrl-C) or POSIX SIGTERM requests a clean stop **between
ticks**, allowing the in-progress SQLite append to finish if possible;
a sudden OS kill is handled later by replay of fully committed SQLite
events, not by attempting to preserve uncommitted in-memory state.

## Android/Termux candidate (requires Termux installed by the owner)

Use a supported Termux build with CPython 3.11+ and enough local
storage. The following is an optional **manual experiment**, not a
command we ran on the owner's device:

    pkg update
    pkg install python git
    git clone --branch feat/r013-portable-cpu-host-termux-r01-20261121 \
      --single-branch https://github.com/Garyu-tt/mojum.git
    cd mojum
    mkdir -p "$HOME/mojum-private"
    chmod 700 "$HOME/mojum-private"

    PYTHONPATH=src python -m mojum.portable_host \
      --db "$HOME/mojum-private/dante.sqlite3" \
      --ticks 120 --interval-ms 1000

Avoid putting the PRIVATE SQLite journal in Android shared Downloads
or exposing it through an HTTP server. Termux app-private home should
remain readable only by the app UID on an uncompromised device. This
does **not** provide at-rest encryption or protection from root access,
backups, filesystem attacks or a rooted/compromised OS.

For later observation on an authorized trusted device, use the existing
local read-only PRIVATE exporter (no automatic sync or remote uploads):

    PYTHONPATH=src python -m mojum.aquarium_trace_export \
      --db "$HOME/mojum-private/dante.sqlite3" \
      --output "$HOME/mojum-private/dante.private.jsonl" \
      --exploration-mode goal_free --sensory-mode toy_probe \
      --allow-private-export

The HTML viewer cannot automatically read Termux app-private files
from Android Chrome. A **separately reviewed Android UI/file bridge**
or explicit owner-controlled copy would be required. Do not silently
copy PRIVATE memory to shared device storage or a public web page.

## Operational invariants

- One local writer is assumed. Competing foreground processes fail
  via Noosphere's SQLite compare-and-append (CAS) if the journal
  advances without their knowledge. This is **not** a distributed
  lease, authenticated device identity or cross-host consensus.
- Invalid tick/interval arguments are rejected before the database is
  opened or the organism is created. Valid bounds are 1..1000 ticks
  and 20..3600000 ms.
- A fixed historical replay ceiling of **10,000 total journal events**
  still exists in the canonical R0.13 Aquarium. Host refuses an
  invocation which would exceed it before writing any requested
  frame. Truly indefinite operation needs a separately validated
  streaming replay and compaction design; merely restarting this
  bounded host cannot bypass this limitation.
- An Android 12+ OS may suspend/kill a Termux foreground/background
  process, and arbitrary power-saving / Doze states affect scheduling.
  On resumption there are **no catch-up bursts** of imaginary past
  ticks: one tick is one actually committed event. This trial does
  not claim 24/7 execution, wakelocks or survival through force-stop.
- The virtual \`brain.placeholder\` reports \`UNIMPLEMENTED\`. Pulse
  shadow learner and synthetic toy deflection do not implement
  human experience, perception, a mobile camera, physical sensors,
  a MuJoCo body, distributed Noosphere or an LLM cognitive engine.
- The database and JSONL remain unencrypted PRIVATE synthetic data;
  the integrity checksum is **not an authentication signature**.
- No native Android APK, boot receiver, foreground service with a
  user-visible notification, persistent system permission or Play
  Store packaging has been built in this branch.

## Regression and acceptance

    PYTHONPATH=src python -m unittest discover -s tests \
      -p test_portable_host.py -v
    PYTHONPATH=src python -m unittest discover -s tests -v

CI covers deterministic monotonic pacing/no missed-time catch-up,
two real Python CLI sessions restoring a single PRIVATE SQLite journal,
owner/mode/head rejection without extra frames, invalid plan without
organism birth, bounded event-budget fail-closed, and SIGTERM during
idle pacing with restart/replay on Linux. Browser Chrome CI remains
part of the stacked #95 candidate.

**Before real Android acceptance:** independently review app-private
filesystem behavior, actual installed Termux Python version and runtime,
screen-off/Doze behavior, energy use, real phone storage constraints,
Android Chrome file-access limitations, and stop/resume consistency.
No physical Pixel 9 has been accessed by this PR.

### Upstream / rollout

Stacked as a **Draft** on #95; both are unmerged. The latest \`main\`
also has a separate graph-validation QA commit (#40), not silently
merged into this branch. Coordinate with the parent branch before
any direct-main release. Do not close organism release #51 or P0 #55
on the strength of this portable synthetic fixture.
