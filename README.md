# Mojum — CPU-first graph-native organism simulator

Mojum is an experimental, standalone **local aquarium for a synthetic organism**. Its long-term goals include GENESIS development, a learnable sensorimotor loop, Dante's virtual body, worlds, and Noosphere memory. The universal graph is a typed runtime contract; Adamantium-node is an **optional** visual editor, not a required dependency.

## What actually runs

The Python 3.11+ **stdlib-only** program works offline on one CPU. It has a versioned typed graph, deterministic GENESIS genotype and acquired phenotype, a synthetic heartbeat, a bounded 2D point-room, a PRIVATE SQLite Noosphere event journal with restart/replay, and a small shadow-only pulse predictor.

Two modes are available in this source candidate:

- **Default `scripted`:** calls to the CLI move by supplied `dx/dy` (default 0.02m/0); the shadow predictor can observe a pulse but **does not drive movement**.
- **Opt-in `goal_free`:** a small deterministic local exploration policy proposes bounded virtual movements and inhibits actions after actual synthetic collision feedback. Optional `toy_probe` sensing adds numeric RGB/range cues, virtual near-contact probing, cue-specific prediction and private learned association **in the same SQLite journal**. It is a limited empirical policy, not a neural brain, autonomous human-like cognition or general object understanding.

The graph node `brain.placeholder` remains **UNIMPLEMENTED**. No MuJoCo body, real touch/camera, physically actuated motors, reliable multi-device failover, authenticated consensus, secure encrypted replication or Android background service is shipped.

## Visual room designer (experimental Draft)

Open [`tools/room_designer.html`](tools/room_designer.html) directly in a modern desktop or mobile browser, **offline and without any server**. Draw 2D obstacles by touch/mouse drag, set the organism's starting point, edit width/height, and export a compatible `mojum.world.point-room-draft/1` JSON. The browser does not create SQLite files, execute simulation ticks, or access private Noosphere memories. Numeric type distinctions such as `4` versus `4.0` are preserved for the next step.

Use `PYTHONPATH=src python -m mojum.room_input mojum-room-draft.json` to validate the exported draft independently, then explicitly create a **new** Aquarium SQLite journal using the documented `--new-room-contract` and `--room-numeric-contract typed_v1` flags. For exact steps and the read-only hash-anchored restart, see [offline room designer instructions](docs/R015_OFFLINE_ROOM_DESIGNER.md). This is only deterministic point-room geometry, not MuJoCo or a neural brain; independent review and main-head release checks remain required.

## Run the current synthetic Aquarium

From a source checkout with CPython 3.11+ and no third-party runtime packages:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante-scripted.sqlite3 --ticks 120 --neural-mode shadow
PYTHONPATH=src python -m mojum.aquarium --db ./dante-scripted.sqlite3 --ticks 30 --neural-mode shadow
```

The second invocation resumes **150 virtual ticks / 151 private journal events** (one bootstrap event plus 150 frames). The default-mode journal cannot be reopened as a different mode or with a different graph/seed/room/clock.

To exercise the **opt-in synthetic developmental** loop, use a **separate** SQLite file:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante-goal-free.sqlite3 --ticks 120 --exploration-mode goal_free --sensory-mode toy_probe
PYTHONPATH=src python -m mojum.aquarium --db ./dante-goal-free.sqlite3 --ticks 30 --exploration-mode goal_free --sensory-mode toy_probe
```

The organism proposes its own tiny point-room motions; you must not supply `--dx/--dy` overrides in this mode. To view committed **PRIVATE** synthetic frames locally, append `--watch` (JSONL on stdout). Do not upload the traces, journals or raw checkpoint metadata to public issue trackers.

To validate/preview a point-room draft without creating any SQLite file, use `PYTHONPATH=src python -m mojum.room_input examples/room_simple_mixed.json`. The preview prints the room dimensions, exact original numeric types and initial world revision.

To create a **brand-new** synthetic room with nondefault dimensions and exact int/float geometry using only the CLI (experimental opt-in, never reuse an existing SQLite file):

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./custom-room.sqlite3 --exploration-mode goal_free --developmental-policy local_cell_v2 --room-numeric-contract typed_v1 --new-room-contract examples/room_simple_mixed.json --ticks 12
```

The file uses ordinary numbers and arrays (e.g. width `4.5`, height `4`, obstacles `[[2.0, 1, 0.125, 2.0]]`). The expert typed `float.hex()` form remains supported, and both authoring styles produce the **same** versioned SQLite wire format. The command prints a `journal_head` to record independently. To reopen the original room in SQLite-enforced read-only mode, without a Python `Room` definition or additional steps:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./custom-room.sqlite3 --exploration-mode goal_free --developmental-policy local_cell_v2 --room-numeric-contract typed_v1 --resume-typed-room --expected-journal-head YOUR_INDEPENDENT_64_HEX_HEAD
```

A positive `--ticks N` on resume explicitly requests further simulated frames and changes the head. New-room creation reserves a fresh name exclusively and refuses existing data; old `bootstrap/1` histories are never inferred or rewritten. See [typed custom-room CLI quickstart](docs/R015_TYPED_ROOM_CLI_QUICKSTART.md). This is a **CPU point-room fixture**, not MuJoCo or a real neural brain.

Windows PowerShell from the checkout:

```powershell
$env:PYTHONPATH = "src"
python -m mojum.aquarium --db .\dante-goal-free.sqlite3 --ticks 30 --exploration-mode goal_free --sensory-mode toy_probe
```

For an offline *evaluation-only* comparison of the toy-contact learner against a frozen model, run:

```sh
PYTHONPATH=src python -m mojum.sensory_benchmark
PYTHONPATH=src python -m unittest discover -s tests -v
```

The benchmark deliberately demonstrates negative transfer when identical RGB hides different material response and **no prediction** for unseen RGB. It proves neither semantic material recognition nor generalization to novel environments.

## Safety, data and continuity limits

- All sensory and contact measurements here are generated by synthetic fixtures. No physical motor capability is granted. No snapshot may reset a real emergency stop.
- SQLite journal events are marked PRIVATE but **not encrypted**; restrict filesystem access. A checkpoint hash is not a signature or an authenticated transfer token.
- Local checkpoint calls fail closed if another writer has advanced the shared journal beyond the latest fully adopted in-memory state. This is not cross-host consensus or a multi-component frozen snapshot.
- The replay budget is currently capped at **10,000 events**; do not leave the fixture running unattended past that limit. The CLI is not a persistent Android service.
- Existing GraphForge prototypes outside this repository are not automatically integrated, and no external development infrastructure is required for this small CPU-first runtime.

## Development and acceptance

The canonical integration scope, negative controls and exact commands are recorded in [R0.13 integration acceptance](docs/R013_CANONICAL_INTEGRATION_ACCEPTANCE.md). Track [P0 sensorimotor development #55](https://github.com/Garyu-tt/mojum/issues/55) and [release acceptance #51](https://github.com/Garyu-tt/mojum/issues/51). A green CI run on a PR is **not** automatically a released feature: main merge and main-head CI must also be verified.

Contributions use scoped code and QA branches with independently checked tests. Avoid committing secrets, production addresses, or PRIVATE memory. **License undecided:** a public repository does not itself grant redistribution rights.
