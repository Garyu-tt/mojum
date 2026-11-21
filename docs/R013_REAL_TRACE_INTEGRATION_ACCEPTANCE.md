# R0.13 canonical organism × real offline trace viewer integration

**Review-only integration branch**, based on direct-to-main R0.13 release
candidate [PR #75](https://github.com/Garyu-tt/mojum/pull/75) and carrying
the additive offline trace/segment work from
[PR #74](https://github.com/Garyu-tt/mojum/pull/74) and
[PR #80](https://github.com/Garyu-tt/mojum/pull/80).

This is the **same** canonical GENESIS/heartbeat/point-room/PRIVATE SQLite
organism, not a separate browser simulation. The new viewer displays the
events and predictions recorded by a real synthetic R0.13 run. The original
`brain.placeholder` still reports `UNIMPLEMENTED`.

## Reproduce locally with Python 3.11+ (stdlib only)

From the repository checkout of this integration candidate:

```sh
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
```

Open `examples/aquarium_trace_viewer.html` in a browser using a `file://`
URL, then select the local file `dante.private.jsonl`. The viewer renders
the *recorded* position, heartbeat phase, bounded developmental movement
intent, actual blocked outcome, RGB/range near-field cue, synthetic probe
intent and measured numerical deflection, and model's previous prediction.
No privileged toy ID, hidden softness or future frame is given to the
learner; **observed** deflection is included only after the runtime requests
and performs a synthetic read-only probe.

For a narrower private segment, add `--start-tick 100 --frame-limit 50`
to the exporter and choose a different output name. Metadata reports
`first_tick`, `next_tick`, `has_more` and the full source journal head.
The exporter **always** makes a consistent read-only SQLite snapshot and
replays/validates the complete journal before selecting any frames.

For a custom synthetic toy scene the Python exporter API also accepts
`sensory_scene=...`; it will refuse to replay a scene that differs from the
bootstrap. The CLI currently supports the standard bundled scene only.

## Boundaries and review

- **Private memory export is opt-in.** The JSONL contains plaintext PRIVATE
  synthetic journal frames; never upload to public GitHub PRs, bug trackers
  or hosted UI. Output is no-clobber and mode 0600 on POSIX.
- The exported SQLite snapshot is replay-verified. An independently pinned
  journal head may be supplied with `--expected-head`. Without a separately
  trusted head, a hash chain is *not authentication*.
- The browser never accesses SQLite or a network. It only reads a
  user-selected local JSONL file into memory. There is no remote telemetry,
  background daemon, physical motor, MuJoCo physics or synchronized device
  mesh in this integration.
- This branch is a candidate **built on #75**, while #75, #74 and #80 are
  still unmerged. Do not independently merge each stacked feature twice.
  The R0.14 four-ray branch #68 is **not** included here.
- The automated Node VM exercises actual inline browser JavaScript with
  PRIVATE JSONL exported from a real two-step R0.13 SQLite Aquarium;
  that is not equivalent to manual Chromium/Firefox/Pixel visual QA.
- Independent review, full final-head CI on Python 3.11/3.12/3.13,
  and product-level release approval remain outstanding. Do not close
  P0 #55 or release-acceptance #51 merely because this viewer works.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_integrated_r013_trace_viewer.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

The new cases enforce end-to-end exact frame equality, initial measured
contact and later prediction, mode/scene/head mismatch refusal, consecutive
segment consistency, read-only WAL source export, real CLI subprocesses
and browser-JavaScript telemetry from actual replay-verified SQLite events.
