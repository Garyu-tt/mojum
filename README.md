# Mojum — CPU-first graph-native organism simulator

Mojum aims to combine GENESIS development, a learnable sensorimotor system, Dante's virtual body, simulated worlds, and Noosphere memory. Universal Graph IR is the standalone typed core; Adamantium-node is an optional editor, not a runtime requirement.

## What actually runs on `main` (synthetic alpha)

The checked-in Python stdlib fixture runs **offline on one CPU**. It has a versioned graph, deterministic inherited/acquired GENESIS phenotype, synthetic heartbeat/interoception, a bounded 2D point-room, an explicit **UNIMPLEMENTED** brain placeholder, a small shadow pulse predictor, and a PRIVATE SQLite journal with compare-and-append, replay, and checkpoint metadata. The predictor learns to estimate the synthetic pulse. The default motor mode uses scripted `dx` and `dy`. The opt-in `developmental_fixture` mode uses the outcomes of earlier committed moves to choose later moves within the point-room.

Release acceptance and the remaining work on the integrated sensorimotor loop are tracked on GitHub. [Acceptance #51](https://github.com/Garyu-tt/mojum/issues/51) · [Sensorimotor integration #55](https://github.com/Garyu-tt/mojum/issues/55)

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

### Replay a committed decision offline

After the six-tick guided run above, export a local trace from the same journal.

```bash
PYTHONPATH=src python -m mojum.aquarium_trace_export --db ./range_guided_PRIVATE.sqlite3 --output ./guided.private.jsonl --motor-mode developmental_fixture --sensory-mode toy_probe --temporal-mode range_guided --allow-private-export
```

Open [aquarium_trace_viewer.html](examples/aquarium_trace_viewer.html) in your
browser and select the exported file. At tick 5 the selected move is north.
The viewer reports a prediction based on one earlier observation and the
recorded change from range bucket 0 to 1.

The exporter verifies the complete journal on a temporary SQLite copy. Use the
same mode flags as the recording. Add `--expected-head SHA256` when verifying
against a separately recorded journal head. For longer histories, add
`--start-tick` and `--frame-limit` together to export a particular segment.
Use a new output filename in a directory with private access. The JSONL is
plaintext and its checksum detects accidental corruption. The viewer rejects traces\nwithout verifiable SHA-256 metadata by default. For genuinely old exports,\nexplicitly enable the unverified legacy checkbox; stripped modern exports\nremain rejected even with this opt-in.

Run regression tests (Python 3.11–3.13 are the CI target):

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture does not need an Internet connection, a GPU, Node.js, Node-RED, or MuJoCo. The SQLite checkpoint is a **checksum-only**, non-authenticated local record, not a portable security credential or complete atomic multi-component snapshot. Journal replay is currently capped at 10,000 events; do not run unattended past this budget.

## Run from an installed wheel

With CPython 3.11+ and a locally built Mojum wheel, install into a virtual environment and run from any working directory:

```sh
python -m pip install --no-index ./mojum-0.1.0-py3-none-any.whl
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 120
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 30
```

Starting from a new journal, the second run reaches tick 150 with 151 stored events. The wheel includes the example genome and graph. The `mojum-aquarium` entry point also accepts `--motor-mode developmental_fixture`. [Offline install instructions](docs/LOCAL_INSTALL_R01.md)

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
