# Mojum — graph-native embodied simulation

**Mojum** is the complete Mechanicus simulation platform: **GENESIS** (genome and development) → neural substrate → Dante's body and sensorimotor loop → **MuJoCo** physics → simulated worlds → **Noosphere** memory.

Mojum is **not just a physics library or a Node-RED fork**. The universal, portable graph is the system's typed source of truth; Adamantium-node is an *optional visual editor*, not a required runtime dependency.

## Scope

- **GENESIS:** inherited genome, acquired changes, deterministic development, phenotype and reproduction experiments.
- **Brain & behavior:** learning, internal state, sensorimotor decisions, independent validation of all cognition claims.
- **Body & interoception:** individual joint definitions, proprioception, heartbeat as a minimal changing internal signal, motor-safety gate.
- **Physics:** CPU-first coupled dynamics, contacts, gravity, collisions and balance using a separate MuJoCo worker.
- **Worlds:** an initial virtual room, world perception, external game/VR adapters as isolated capabilities.
- **Noosphere:** episodic/semantic memory, provenance, privacy, independent replicas and reproducible dataset views.
- **Universal Graph IR:** nested graphs, typed ports, node notes/roadmap, deterministic execution, versioning and reproducible checkpoints.
- **Agent orchestration:** distributed, CPU-first, permissioned components without a single compulsory host.

## Truthful status

**Experimental synthetic alpha, not a complete digital organism.** The CPU-only standalone Aquarium already runs a deterministic graph / GENESIS fixture, synthetic heartbeat, obstacle-aware point-room and a PRIVATE local SQLite event journal with replay on restart. A small pulse predictor learns only in **shadow mode**: it does **not** control the body. The graph's `brain.placeholder` remains `UNIMPLEMENTED`. No MuJoCo body, trusted cross-device replication, autonomous cognition or Android background-service support is shipped. Separate unmerged PRs explore sensory learning, spectator UI and portable checkpoint formats; their features are **not** part of current `main`.

### Local fixture startup

With CPython 3.11+ and local `setuptools>=61` build tooling already installed:

```sh
python -m pip install --no-index --no-build-isolation --no-deps .
mojum-aquarium --db ./dante-fixture.sqlite3 --ticks 120 --neural-mode shadow
mojum-aquarium --db ./dante-fixture.sqlite3 --ticks 30 --neural-mode shadow
```

The second command resumes the same local journal (150 simulation ticks and 151 stored events). This example does **not** make the program a persistent background service. If offline build tooling is missing, installation fails without silently contacting an online package index. Details, limits and overrides: [local install R0.1](docs/LOCAL_INSTALL_R01.md).

Existing GraphForge prototypes are external and **not automatically integrated**. Do not treat synthetic sensor traces as real visual perception or proof of consciousness.

## Development

Five equal peers use an independent 50/50 coin each run: **heads → BUILD**, **tails → independent QA**. When the selected queue is unavailable, use the opposite role immediately. Never approve one's own code. Changes go through focused branches, tests and independently reviewable PRs, with concrete evidence.

Do not copy secrets, infrastructure addresses or private memory into this public repository. Physical actuation is disabled by default, and emergency stops cannot be reset through snapshot restoration.

**License:** pending an explicit owner decision; visibility alone does not grant a license.
