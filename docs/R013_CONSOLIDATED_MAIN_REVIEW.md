# R0.13 single direct-main candidate: canonical synthetic Aquarium + PRIVATE offline viewer

**Review-only. Not an organism release. Never auto-merge.**

This RC condenses R0.13 goal-free / toy-probe single-journal Aquarium with the
read-only SQLite -> PRIVATE JSONL -> local HTML observer. All code was reused
from separately tested PRs. Newer #75 center-ray wall occlusion changes were
reconciled explicitly in five files after cloning viewer #91's tested head.
Compare and test the complete final branch against main, not only its last
small stacked pull request.

## What the direct-main diff includes

- CPU-only single-agent point-room synthetic Aquarium with GENESIS seed,
  an ongoing heartbeat, measured motor outcomes, optional goal-free movement,
  toy numeric proximity, requested synthetic contact deflection and subsequent
  empirical prediction, fully journaled in a single PRIVATE SQLite Noosphere.
- Newer canonical #75 center-ray wall occlusion: a solid motor-room wall
  suppresses hidden-toy RGB/range and probe requests through the wall. The
  exact sensor-occlusion schema is pinned in the journal bootstrap and
  incompatible pre-mask private experimental journals fail closed.
- A read-only SQLite snapshot including uncheckpointed WAL, full deterministic
  replay of source history, optional externally known expected journal head,
  explicit opt-in PRIVATE plaintext JSONL output (new path, 0600 POSIX),
  strict bounds and optional explicit time segments.
- One-file offline HTML viewer: recorded location/heartbeat, real synthetic
  probe measurement, learned prediction, motor intent and blocked state.
  Built-in browser WebCrypto checks the exact JSONL frame bytes via SHA-256
  in the final checkpoint before playback. Corrupt/truncated files fail
  closed; legacy recordings lacking SHA are visibly unverified.
- Real Chrome browser CI on desktop width 1280 CSS px and exact emulated
  mobile width 390 CSS px using installed Chrome/ChromeDriver (no downloads
  of browser automation frameworks, public network server or artifacts).

Important: the browser represents a synthetic point-room organism, **not**
a separate JS creature, full trained neural brain, MuJoCo avatar,
consciousness, physical touch, mobile background daemon, authenticated
distributed memory, quorum or safe physical actuator.

## Reproduce on ordinary CPU

Run from this branch checkout with Python 3.11+ and stdlib:

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante.sqlite3 --ticks 120 \
      --exploration-mode goal_free --sensory-mode toy_probe

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante.sqlite3 --ticks 30 \
      --exploration-mode goal_free --sensory-mode toy_probe

    PYTHONPATH=src python -m mojum.aquarium_trace_export \
      --db ./dante.sqlite3 --output ./dante.private.jsonl \
      --exploration-mode goal_free --sensory-mode toy_probe \
      --allow-private-export

Open examples/aquarium_trace_viewer.html in your browser and use the
file selector to import the resulting local dante.private.jsonl.

For a restricted local segment add --start-tick 100 --frame-limit 50,
using a fresh output filename; continuation metadata includes next_tick.
For a trusted historical head add --expected-head with an **independently
stored** 64-character SHA-256 hash of the entire source Noosphere journal.
A checksum embedded in an attacker-controlled file is not a signature.

Do NOT upload any real/private SQLite DB, JSONL trace, credentials, or
device telemetry to GitHub or an external web page.

## Release acceptance / remaining blockers

1. Github compare baseline main at 935b91ec4d33a96fd59fb97a5937b6e1120b7c97:
   candidate built as a descendent with no main changes. Verify ahead/behind
   and file list again on the final head when the direct-main PR is created.
2. Full standard GitHub test matrix on the **exact final direct-main SHA**:
   Python 3.11/3.12/3.13 Aquarium, motor and mesh tests (9 jobs);
   plus one real desktop/CDP-mobile Chrome job (total 10).
3. Check actual logs contain no unclosed sqlite3 ResourceWarnings. The
   test-only fixture closures were originally introduced in #86.
4. R0.13 revised center-ray wall mask must pass through deterministic
   source replay and the actual JSONL/HTML viewer, not leak hidden toy
   identity/material/RGB via any occluded virtual touch.
5. Preserve default legacy scripted mode and reject incompatible
   previously generated PRIVATE scenes/journal manifests explicitly.
6. Distinct human account review of code, private-data threat model and
   known unsupported device/platform requirements. The agent's repeated
   self-tests or a shared GitHub login are NOT an independent reviewer.
7. Run real physical Android/Pixel browser file chooser/lifecycle/layout QA
   before advertising Android support. Chrome Linux CDP emulation does not
   prove that physical Android Chrome, Termux, or an APK works.
8. Release #51 and P0 #55 remain independently blocked until genuine
   intended neural/embodied functionality and owner acceptance are met.

## Provenance and duplicate-merge warning

Upstream R0.13 canonical RC #75 consolidates #59/#61/#63/#65/#69/#70/
#72/#73 and R0.9 #33/#56. Sensor wall-occlusion commits
f79d341 -> 7f388c9 were copied from #75 HEAD to this candidate; the
source #75 branch was **not** modified.

Real private SQLite exporter + viewer originated in #74; bounded segment
projection in #80; compatible canonical source integration in #82; test
SQLite resource-handle cleanup #86; real desktop/mobile headless Chrome
#87; frame SHA-256 and browser WebCrypto integrity #91.

DO NOT merge all these PRs separately after this candidate lands: doing so
duplicates source paths and can accidentally overwrite the current
occlusion-enabled sensory implementation. If some predecessor is separately
accepted, rebase/reconcile and rerun the full exact-head CI.

Separate R0.14 ray/outcome experiments (#68 and peers), local multi-UID
writer socket services and multi-node failure recovery are OUTSIDE this RC.
