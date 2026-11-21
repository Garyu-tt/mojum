# Mojum — CPU-first graph-native organism simulator

Mojum aims to combine GENESIS development, a learnable sensorimotor system, Dante's virtual body, simulated worlds, and Noosphere memory. Universal Graph IR is the standalone typed core; Adamantium-node is an optional editor, not a runtime requirement.

## What actually runs on `main` (synthetic alpha)

The checked-in Python stdlib fixture runs **offline on one CPU**. It has a versioned graph, deterministic inherited/acquired GENESIS phenotype, synthetic heartbeat/interoception, a bounded 2D point-room, an explicit **UNIMPLEMENTED** brain placeholder, a small **shadow-only** pulse predictor, and a PRIVATE SQLite journal with compare-and-append, replay, and checkpoint metadata. The predictor updates, but **does not choose movements**. The CLI supplies scripted `dx/dy`; no general learned organism, MuJoCo dynamics, real motors, secure mesh, encrypted replicas, or atomic live-state migration is implemented.

The accepted scope is tracked in [issue #51](https://github.com/Garyu-tt/mojum/issues/51); [P0 issue #55](https://github.com/Garyu-tt/mojum/issues/55) tracks the missing goal-free sensory → learned intention → action → feedback loop. Experimental PRs are **not** part of `main` until merged and reverified.

## Run the current aquarium

From the repository root, with Python 3.11+ and no third-party dependencies:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120 --neural-mode shadow
```

Repeat the command to replay and append 120 more frames to the **same** private local SQLite file. The JSON checkpoint reports `tick`, `stored_events`, `journal_head`, and a status explicitly labelling synthetic shadow learning. On a new DB, 120 ticks should produce **121** events (one bootstrap + 120 frames); a second 120-tick run should produce **241**. Use a fresh path for an independent organism fixture; do not publish the SQLite file, as it contains PRIVATE synthetic event records. For a no-learning control, use a **different DB path** and `--neural-mode disabled`; the mode is pinned in the bootstrap and cannot silently change on reopen.

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m mojum.aquarium --db .\dante_fixture.sqlite3 --ticks 120 --neural-mode shadow
```

Run regression tests (Python 3.11–3.13 are the CI target):

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture does not need an Internet connection, a GPU, Node.js, Node-RED, or MuJoCo. The SQLite checkpoint is a **checksum-only**, non-authenticated local record, not a portable security credential or complete atomic multi-component snapshot. Journal replay is currently capped at 10,000 events; do not run unattended past this budget.

## Run from an installed wheel

With CPython 3.11+ and a local Mojum wheel, install into a virtual environment and run from any working directory:

```sh
python -m pip install --no-index ./mojum-0.1.0-py3-none-any.whl
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 120
mojum-aquarium --db ./dante_fixture.sqlite3 --ticks 30
```

Starting with a new journal, the second run brings it to tick 150 with 151 stored events. The wheel includes the example genome and graph. [Offline build and install instructions](docs/LOCAL_INSTALL_R01.md)

## Architecture and development

- **GENESIS:** inherited and acquired genes, deterministic phenotype changes.
- **Graph IR:** typed graph execution; independent of optional IDEs.
- **Interoception / world:** synthetic heartbeat and point-room; no calibrated muscles or physics.
- **Neural:** shadow pulse predictor only; no autonomous motor decision in released main.
- **Noosphere:** local PRIVATE SQLite event journal, replay and bounded dataset projections; no authenticated distributed continuity.
- **Future:** goal-free measured sensorimotor learning, coupled body/MuJoCo, privacy-preserving distributed replicas.

Five equal peers work through scoped GitHub issues and PRs, independent review, reproducible CPU tests, and final-head CI. Do not treat a green stacked branch as a released feature. No secrets, infrastructure addresses, or private memories belong in this public repository. Physical actuation is disabled and no snapshot may reset an emergency stop.

**License:** undecided; repository visibility does not grant a license.
