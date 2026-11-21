# Mojum architecture — implemented boundaries and intended design

Mojum is the whole simulation system, **not** another name for MuJoCo, Dante's robot rig, or an Adamantium-node graph editor. This document distinguishes the **released `main` CPU-first synthetic aquarium** from proposals and unmerged experimental pull requests. For the release-gate snapshot and remaining blockers, see [acceptance issue #51](https://github.com/Garyu-tt/mojum/issues/51).

## Intended architecture (not an implementation claim)

GENESIS genome/development → neural sensorimotor substrate ↔ interoception/body → physics/world → measured sensory outcomes → Noosphere memory → future learned intentions.

Universal Graph IR defines versioned, typed topology. Adamantium-node is an optional visual editor, and a distributed MAS/Continuity Mesh may eventually coordinate placement. **Neither an editor, a remote host, nor an accelerator is required to run the current offline fixture.**

## What the current `main` actually implements

| Component | Released synthetic-alpha behavior | Not yet provided |
| --- | --- | --- |
| Universal Graph IR | Executable `mechanicus.universal-graph/1` DAG with typed ports; an isolated opt-in v2 graph audit that does **not** execute | Executable arbitrary plugin code, nested/cyclic scheduling, enforced per-node privacy labels |
| GENESIS | Deterministic inherited/acquired seed ledger, versioned phenotype and replay of acquired-gene events | Full neuron-population development, physiological brain regions, body growth |
| Neural | A bounded three-feature delayed-pulse predictor with genuine **shadow-only** online updates; `brain.placeholder` returns `UNIMPLEMENTED` | Learning-driven motor intentions, general perception or autonomous organism |
| World/body | Deterministic 2D point-room and swept obstacle rejection; opt-in developmental motor-play policy based on committed prior action outcomes; separate pure-data motor-safety contract | MuJoCo, coupled articulated body, force/contact calibration or physical actuation |
| Noosphere | Local **PRIVATE SQLite** append-only event journal, compare-and-append, hash-chain replay, recorded/observed/valid times, and default PUBLIC-only dataset projection | Encrypted storage, authenticated writer enrollment, independently durable distributed replicas, quorum or partition-safe failover |
| Checkpoint | Hash/checksum-based metadata of current fixture state and journal head | Atomic multi-component snapshot, authenticated portable recovery or running-state migration |

The aquarium simulation uses the original six typed fixture nodes: `genesis.seed`, `body.heartbeat`, `sensory.interoception`, `world.room`, `brain.placeholder`, and `memory.episode`. By default motion is **scripted `dx/dy`**. The separately opt-in `--motor-mode developmental_fixture` selects virtual movements from its own previously committed success/block history, not from a learned neural brain or privileged room geometry. The `brain.placeholder` node remains `UNIMPLEMENTED`; the shadow pulse predictor remains observational. See [developmental motor-play contract](DEVELOPMENTAL_R013.md).

The SQLite journal is authoritative for committed synthetic events. Reopening the same journal reconstructs the fixture through replay, checks the bootstrap against the same genotype/graph/room/clock, and rejects divergent history. Local hash chains and checkpoint metadata **do not authenticate** an attacker-controlled file: independent trusted anchors and authenticated replication are separate work. Existing synthetic journal replay is intentionally capped at **10,000 events**.

## Contracts and safety invariants

1. Keep the canonical runnable graph at `mechanicus.universal-graph/1`; unknown node kinds do not execute. A second incompatible wire format cannot reuse that identifier. Executable v1 rejects undeclared visibility metadata instead of silently ignoring it; the isolated v2 audit is non-executable and does not grant access control (see [issue #25](https://github.com/Garyu-tt/mojum/issues/25)).
2. The DAG executor has explicit typed ports and no hidden cyclic scheduler. Graph nodes include machine-readable notes. Any new shape requires a separately versioned contract and replay migration.
3. Virtual logical ticks and `time_ns` are deterministic **simulation timestamps**, never wall-clock measurements or evidence of a real robot's movements.
4. Inherited and acquired GENESIS state must retain version, identity, and provenance. Replaying a journal must not apply a mutation twice or silently change the organism ID.
5. Motor-safety proposals must pass a separate gate before any future actuator integration. The current gate is a **pure-data fixture**, not a physical e-stop controller; restoring state must never silently clear an actual emergency stop.
6. Noosphere events are append-only; PRIVATE data is excluded from default public projections. Protect local SQLite file permissions and never publish journals or exports as demo assets.
7. CPU and Python standard library are the supported baseline. GPU, GUI, Node.js, remote networking and MuJoCo are not prerequisites for the current sample.
8. Synthetic heartbeat, a trained pulse predictor, virtual movement and scripted school tasks are **not** evidence of consciousness, general intelligence or anatomical realism.

The local `mojum-aquarium` and `mojum-fixture` commands are also available from the optional offline wheel, with bundled synthetic fixtures; see [local installation](LOCAL_INSTALL_R01.md). A local wheel is not a mobile release or a distributed organism host.

## Interfaces planned, not yet connected end-to-end

Future boundaries include `mechanicus.body/1` (rig and DOFs), `mechanicus.physics/1` (MuJoCo state and contacts), `mechanicus.neural/1` (sensory learning and safe intentions), `mechanicus.world/1` (external environments), and `mechanicus.simulation-snapshot/1` (atomic revision-consistent state). These are **planned contracts**, not APIs that can currently be assumed operational in released `main`. Experimental PRs are not shipped features until independently reviewed and merged.

## Reproduce the single-host fixture

From the repository root with Python 3.11+:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120 --neural-mode shadow
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 30 --neural-mode shadow
PYTHONPATH=src python -m unittest discover -s tests -v
```

For a separate **new** PRIVATE journal, the opt-in synthetic motor-play fixture is:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./motor_fixture_PRIVATE.sqlite3 --ticks 20 --motor-mode developmental_fixture
```

On a **new** database, 120 frames produce 121 events (bootstrap + frames), then 30 further frames give 151. Use another DB for a different organism or `--neural-mode disabled` control. Avoid uploading the PRIVATE journal. See [README](../README.md) for CLI/Windows details and [issue #11](https://github.com/Garyu-tt/mojum/issues/11) for the incomplete one-device product release gate.
