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

**R0.1 bootstrapping.** Work is starting from an empty repository. Existing GraphForge R0.1–R0.14 prototypes were developed externally and are **not automatically integrated**. The first PR is intended to establish a *small CPU-only graph interpreter* with a synthetic heartbeat, static world fixture, explicit placeholder brain and in-memory episode sink. It does **not** include a trained brain, Noosphere persistence, calibrated body, or a MuJoCo integration.

## Development

Five equal peers use an independent 50/50 coin each run: **heads → BUILD**, **tails → independent QA**. When the selected queue is unavailable, use the opposite role immediately. Never approve one's own code. Changes go through focused branches, tests and independently reviewable PRs, with concrete evidence.

Do not copy secrets, infrastructure addresses or private memory into this public repository. Physical actuation is disabled by default, and emergency stops cannot be reset through snapshot restoration.

**License:** pending an explicit owner decision; visibility alone does not grant a license.
