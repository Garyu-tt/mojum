# Mojum — CPU-first graph-native organism simulator

Mojum aims to combine GENESIS development, a learnable sensorimotor system, Dante's virtual body, simulated worlds, and Noosphere memory. Universal Graph IR is the standalone typed core; Adamantium-node is an optional editor, not a runtime requirement.

## What actually runs on `main` (synthetic alpha)

The checked-in Python stdlib fixture runs **offline on one CPU**. It has a versioned graph, deterministic inherited/acquired GENESIS phenotype, synthetic heartbeat/interoception, a bounded 2D point-room, an explicit **UNIMPLEMENTED** brain placeholder, a small shadow pulse predictor, and a PRIVATE SQLite journal with compare-and-append, replay, and checkpoint metadata. The predictor learns to estimate the synthetic pulse. The default motor mode uses scripted `dx` and `dy`. The opt-in `developmental_fixture` mode uses the outcomes of earlier committed moves to choose later moves within the point-room.

Release acceptance and the remaining work on the integrated sensorimotor loop are tracked on GitHub. [Acceptance #51](https://github.com/Garyu-tt/mojum/issues/51) · [Sensorimotor integration #55](https://github.com/Garyu-tt/mojum/issues/55)

## Exact-head CI merge gate (acceptance tooling)

`mojum.merge_gate` turns a PR snapshot into a `ready` / `defer` / `reject`
verdict for one exact head and base commit and never performs a merge. Pass the
head you intend to merge and the current accepted `main` commit:

```sh
SHA=$(gh pr view 419 --json headRefOid -q .headRefOid)
BASE=$(gh api 'repos/{owner}/{repo}/git/ref/heads/main' --jq .object.sha)
PYTHONPATH=src python3 -m mojum.merge_gate --pr 419 \
  --expected-head-sha "$SHA" --expected-base-sha "$BASE"
```

For `main`, the gate always requires the nine Aquarium, Motor Gate, and Mesh
jobs for Python 3.11, 3.12, and 3.13. Use `--require JOB` for any additional
applicable check identified in the reviewed CI workflow.

Exit `0` means ready, `2` means defer while CI is incomplete, `1` means reject.
See [docs/MERGE_GATE_R01.md](docs/MERGE_GATE_R01.md) for the verdicts and
the GitHub acceptance policy.

## Offline room designer (experimental)

Open [the local SVG editor](tools/room_designer.html) with its three adjacent JavaScript files to draw a synthetic 2D point-room, move the start marker and rectangles, preserve exact decimal tokens, save/reopen JSON and preview up to 512 collision-only steps. Undo/Redo and a bounded 64-snapshot history work without a server. For a **single portable HTML file**, use `python tools/package_room_designer.py --out room-designer-offline.html`. See [editor limitations](docs/R015_VISUAL_ROOM_DESIGNER.md).

**Input-only, offline authoring at this stage:** the typed-room JSON CLI required to create a custom Aquarium SQLite journal has not been merged into `main`; this editor does not claim runtime integration, MuJoCo or real physical contact.

## Run the current aquarium

From the repository root, with Python 3.11+ and no third-party dependencies:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120 --neural-mode shadow
```

Repeat the command to replay and append 120 more frames to the **same** private local SQLite file. The JSON checkpoint reports `tick`, `stored_events`, `journal_head`, and a status explicitly labelling synthetic shadow learning. A run from a new journal records **121** events (one bootstrap and 120 frames). A second 120-tick run records **241** in total. Use a fresh path for an independent fixture and keep its SQLite records private. For a scripted control with the pulse predictor disabled, start a separate journal with `--neural-mode disabled`. Reopen each journal with its original mode.

If two processes open the same journal, an append by one makes the other process's in-memory state stale. Reopen the journal in that process before taking a checkpoint or writing another frame or GENESIS change.

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m mojum.aquarium --db .\dante_fixture.sqlite3 --ticks 120 --neural-mode shadow
```

### Synthetic motor exploration

Start a separate journal for the opt-in mode and use the same mode to resume it.

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./motor_play_PRIVATE.sqlite3 --ticks 120 --motor-mode developmental_fixture
PYTHONPATH=src python -m mojum.aquarium --db ./motor_play_PRIVATE.sqlite3 --ticks 30 --motor-mode developmental_fixture
```

The second command reaches tick 150 with 151 stored events. The policy explores virtual cardinal moves and uses their recorded success or blockage to guide later choices. Use `--dx` and `--dy` with scripted mode. [Motor exploration contract](docs/DEVELOPMENTAL_R013.md)

### Synthetic contact learning

Use a fresh local journal to pair developmental motor play with numeric
near-field sensing and virtual probes. Run the same mode to cold-replay and
continue the journal.

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./toy_probe_PRIVATE.sqlite3 --ticks 1 --motor-mode developmental_fixture --sensory-mode toy_probe
PYTHONPATH=src python -m mojum.aquarium --db ./toy_probe_PRIVATE.sqlite3 --ticks 1 --motor-mode developmental_fixture --sensory-mode toy_probe
```

The first tick measures a deflection of `0.75` for RGB cue `220:70:30`.
The second uses that prediction and skips the redundant probe. The JSON
checkpoint reports the learned mean and observation count in
`toy_contact_state`. The wheel command `mojum-aquarium` accepts the same flags.
Keep the SQLite journal and its learned state private.

To let measured changes in distance guide later movement, start another
fresh journal with `--temporal-mode range_guided`. For example, run five ticks
and then resume for one tick with the same flags.

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./range_guided_PRIVATE.sqlite3 --ticks 5 --motor-mode developmental_fixture --sensory-mode toy_probe --temporal-mode range_guided
PYTHONPATH=src python -m mojum.aquarium --db ./range_guided_PRIVATE.sqlite3 --ticks 1 --motor-mode developmental_fixture --sensory-mode toy_probe --temporal-mode range_guided
```

In the default fixture the resumed tick chooses north using a previously
measured range change. The control with `--temporal-mode none` chooses east
at the same starting position and motor history. The guided mode keeps the
usual 0.125 m virtual steps and scheduled exploration. Its coarse distance
predictions can disagree across positions or rooms. Checkpoints report
`temporal_range_state` for this mode.

### Versioned epoch continuation (opt-in)

For a separate bounded long-run motor journal, use the new `local_cell_v4`
policy. It carries an absolute logical tick and commits an explicit epoch
marker at the 9,999-tick boundary; existing scripted, v2 and v3 journals are
not reinterpreted. The default profile is sensorless; the versioned
`toy_probe_v2` table can be composed with v4 for numeric near-field evidence.
The legacy `toy_probe` table remains incompatible with v4. Use explicit
selectors and repeat bounded batches:

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./local_cell_v4_PRIVATE.sqlite3 \
  --ticks 1000 --motor-mode local_cell_v4 --neural-mode disabled

PYTHONPATH=src python -m mojum.aquarium --db ./local_cell_v4_sensory_PRIVATE.sqlite3 \
  --ticks 1000 --motor-mode local_cell_v4 --sensory-mode toy_probe_v2 \
  --neural-mode disabled
```

For a wall-paced foreground session on the same one-device journal, use the
bounded host with the v4 selectors explicit:

```bash
PYTHONPATH=src python -m mojum.portable_host \
  --db ./local_cell_v4_PRIVATE.sqlite3 --ticks 1000 --interval-ms 1000 \
  --motor-mode local_cell_v4 --sensory-mode none --temporal-mode none \
  --neural-mode disabled
```

The host prints metadata only and admits the epoch marker in its event-budget
preflight; repeat the bounded command to continue after a clean restart.

For the versioned sensory profile, use `--sensory-mode toy_probe_v2` with the
same `--temporal-mode none` selector. Its bootstrap pins the v4 and sensory v2
contracts, and the wire inspector reports `local_cell_v4+toy_probe_v2`.

The v4 path is still bounded by the 100,000-event local journal budget and the
current checksum-only replay contract. It is not a neural brain, an
authenticated segment archive, a physical device runtime, or proof of
conscious experience. See [the epoch contract](docs/LOCAL_CELL_V4_EPOCH_R01.md).

### Bounded foreground host

For a paced, restartable one-device session around the same canonical loop,
use the bounded foreground host. It commits only real ticks, stops between
SQLite appends, refuses runs beyond the fixed replay budget, and prints
metadata rather than PRIVATE frames or learner state:

```bash
PYTHONPATH=src python -m mojum.portable_host --db ./host_PRIVATE.sqlite3 --ticks 20 --interval-ms 50
```

Repeat with the same path to resume. This is still a synthetic CPU fixture,
not a neural brain, physical actuator, Android service, or 24/7 guarantee.
See [bounded host details](docs/PORTABLE_CPU_HOST_R013.md).

The same host can pace the opt-in `local_cell_v4` policy. The sensorless
profile uses the versioned epoch marker at tick 9,999 and accounts for those
markers before writing so the 100,000-event bound is never crossed:

```bash
PYTHONPATH=src python -m mojum.portable_host \
  --db ./local_cell_v4_host_PRIVATE.sqlite3 --ticks 20 --interval-ms 50 \
  --motor-mode local_cell_v4 --sensory-mode none --temporal-mode none
```

For a separately versioned contact-association retention experiment, use a
fresh journal with `--sensory-mode toy_probe_v2`. It keeps the original
`toy_probe` wire contract unchanged, bounds live numeric associations to 128
cue records with deterministic eviction, and records forgotten evidence in
the PRIVATE checkpoint. See [the R0.15 contract](docs/SENSORY_ASSOCIATIONS_V2_R015.md).

### Replay a committed decision offline

After the six-tick guided run above, export a local trace from the same journal.

```bash
PYTHONPATH=src python -m mojum.aquarium_trace_export --db ./range_guided_PRIVATE.sqlite3 --output ./guided.private.jsonl --motor-mode developmental_fixture --sensory-mode toy_probe --temporal-mode range_guided --allow-private-export
```

Open [aquarium_trace_viewer.html](examples/aquarium_trace_viewer.html) in your
browser and select the exported file. At tick 5 the selected move is north.
The viewer reports a prediction based on one earlier observation and the
recorded change from range bucket 0 to 1.

Current exports must retain their SHA-256 frame metadata: the viewer rejects
a modern checkpoint whose proof has been stripped, even if the JSONL frames
themselves still parse. Only a genuinely older checkpoint without the current
trace-range marker can be opened through the viewer's explicit legacy
checkbox, and it remains visibly marked as unverified.

The exporter verifies the complete journal on a temporary SQLite copy. Use the
same mode flags as the recording. Add `--expected-head SHA256` when verifying
against a separately recorded journal head. For longer histories, add
`--start-tick` and `--frame-limit` together to export a particular segment.
Use a new output filename in a directory with private access. The JSONL is
plaintext and its checksum detects accidental corruption. See the [offline
viewer guide](docs/REAL_AQUARIUM_TRACE_VIEWER_R01.md) for the exact integrity
and legacy-format boundaries.

Run regression tests (Python 3.11–3.13 are the CI target):

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture does not need an Internet connection, a GPU, Node.js, Node-RED, or MuJoCo. The SQLite checkpoint is a **checksum-only**, non-authenticated local record, not a portable security credential or complete atomic multi-component snapshot. Reopening streams verified history in bounded batches, so replay no longer materializes the whole projection, but the journal remains capped at 100,000 events with CPU time and disk use growing linearly; do not run unattended past this budget. See [streaming replay contract](docs/STREAMING_REPLAY_R01.md).

## Run from an installed wheel

With CPython 3.11+ and a locally built Mojum wheel, install into a virtual environment and run from any working directory:

```sh
python -m pip install --no-index ./mojum-0.1.0-py3-none-any.whl
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 120
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 30
```

Starting from a new journal, the second run reaches tick 150 with 151 stored events. The wheel includes the example genome and graph. The `mojum-aquarium` entry point also accepts `--motor-mode developmental_fixture`. [Offline install instructions](docs/LOCAL_INSTALL_R01.md)

To reopen against a separately recorded head, pass the checkpoint's
`journal_head` as `--expected-head`. The flag requires an existing journal and
fails with exit status 2 before appending (or creating a file) when the head is
malformed, missing or does not match the stored chain.

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 1 \
  --expected-head 0123...cdef
```

The anchor is an independent integrity check for accidental or concurrent
rewrites; it is not authentication.

## Identify a journal's stored policy

```bash
PYTHONPATH=src python -m mojum.aquarium_wire --db ./dante_fixture.sqlite3
```

The read-only command checks the SQLite event chain and prints the stored
policy schema, frame count and head. Use `--expected-head` to verify against
a separately recorded head. The experimental journals from #112 and #115
report distinct `local_cell_v2_a_unreleased` and `local_cell_v2_b_unreleased`
formats even though the original CLIs used the same mode name. Keep each
journal with the matching runtime for deterministic replay.

`python -m mojum.aquarium_wire --list-policies` prints every distinct
persisted identity (selectors, state schema, learning schema, intent source)
without opening a journal. The list comes from `mojum.policy_identity`, which
fails at import time if two identities claim the same selector set, public
configuration, or schema so no future mode can silently impersonate another.

## Audit measured motor mobility

A blocked-move count alone cannot show whether a developmental policy actually
explored the room. The read-only audit verifies the journal chain and reports
attempts, successful moves, idle fraction, longest rest streak, distinct
positions, coverage span and path length, plus versioned rest-collapse and
revisit-concentration gates:

```bash
PYTHONPATH=src python -m mojum.motor_mobility --db ./motor_play_PRIVATE.sqlite3
```

`--expected-head` checks an independently recorded chain head. The command
never writes to the journal and rejects scripted or noncanonical journals.
[Audit contract](docs/DEVELOPMENTAL_R013.md)

## Explicit local-cell history compatibility

Use `--motor-mode local_cell_v2b` for a new v2B journal. The historic
`local_cell_v2` spelling resumes only existing v2B history. The exact
v2B SQLite schema remains compatible with the original #115 runtime.
For original #112 history, use `--motor-mode local_cell_v2_a_archive
--ticks 0` and the original genome, graph, and neural mode. Both profiles
use the exact built-in point-room and frame-only journals without toy sensing.

To transfer a verified v2A history into a separate new v2B file, use
`python -m mojum.migration_v2a_v2b` with the original genome and graph,
an independently recorded `--expected-source-head`, and the explicit
`--create-new-journal` flag. The transfer compares every frame and the
learned state, with a limit of 1,000 frames. It refuses an existing
destination and preserves the source file. Both modes can also produce
replay-verified PRIVATE JSONL via the offline exporter.

Use `--motor-mode local_cell_v3` for the distinct v3 policy. It retains up
to 512 active cells and evicts the least recently observed cell when a
new one arrives at capacity. The journal records evictions and forgotten
observations. The SQLite journal still has a 10,000-event budget.

To continue earlier v2B experience as v3, use the same transfer flags
with `python -m mojum.migration_v2b_v3`. The command opens v2B read-only
and publishes a new v3 file only if every historical frame and retained
observation matches, with zero evictions during the transfer.

### Local Cell/3-escape (isolated, library-only)

`mojum.cell_policy_v3_escape` is an opt-in profile that re-probes its own
blocked evidence after a bounded pause instead of resting forever once every
move in a cell was blocked (issue #106). It is **not** wired into the
Aquarium manifest, CLI motor modes, SQLite wire format or existing journals.
[Escape profile contract](docs/R014_LOCAL_CELL_REST_ESCAPE.md)

## Architecture and development

- **GENESIS:** inherited and acquired genes, deterministic phenotype changes.
- **Graph IR:** typed graph execution; independent of optional IDEs.
- **Interoception / world:** synthetic heartbeat and point-room; no calibrated muscles or physics.
- **Neural** predicts the synthetic pulse in shadow mode.
- **Motor exploration** uses a bounded point-room policy when explicitly enabled.
- **Noosphere:** local PRIVATE SQLite event journal, replay and bounded dataset projections; no authenticated distributed continuity.
- **Future work** includes integrated neural motor learning, a coupled MuJoCo body, and privacy-preserving distributed replicas.

Five equal peers work through scoped GitHub issues and PRs, independent review, reproducible CPU tests, and final-head CI. Do not treat a green stacked branch as a released feature. No secrets, infrastructure addresses, or private memories belong in this public repository. Physical actuation is disabled and no snapshot may reset an emergency stop.

**License:** undecided; repository visibility does not grant a license.

## Offline trace import safety

The viewer verifies the SHA-256 of frames in modern Aquarium JSONL exports and fails closed if the checksum, export status, room or range metadata is incomplete. Truly older traces without proof metadata are **rejected by default**; a user must explicitly enable the unchecked `legacy` checkbox to view them. Such traces are visibly marked **unverified**, and SHA-256 integrity is not authentication or an authorization to disclose PRIVATE history.
