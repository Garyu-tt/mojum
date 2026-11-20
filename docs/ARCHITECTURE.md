# Mojum architecture — source of truth and boundaries

Mojum is the **whole simulation system**, not an alternative label for MuJoCo or Dante's robot model.

Intended dependency direction:

GENESIS genome/development → neural substrate ↔ sensory/interoception ↔ body/spinal control → physics/world → sensory observations → Noosphere memory.

**Universal Graph IR** is the versioned, typed topology and data contract. **Adamantium-node** is an optional recursive graphical editor/flow host. **MAS** may provide authorized distributed placement. The drawing describes intended boundaries, not active integrations.

## Invariants

1. Graph schema is `mechanicus.universal-graph/1`; stable IDs and typed endpoints. Unknown kinds must never execute.
2. Each graph node has machine-readable notes (purpose, ideas, blockers, history). The R0.1 runtime supports DAGs only. Cycles/nesting need explicit scheduler and revisions in a future release.
3. Deterministic logical clock (`simTick`, `time_ns`). Never confuse a virtual-time test with wall-clock or physical motion.
4. GENESIS genotype/phenotype mapping must preserve inherited vs acquired genes and source provenance.
5. Physics (MuJoCo) will be authoritative for the coupled body, contact and force calculations, not visual cards or a local joint emulator.
6. Neural outputs are *proposals* passing a separate motor-safety gate. Never clear emergency stop through checkpoint restore.
7. Noosphere events will be append-only and permissioned, with separate recorded/observed times, independent replicas, and dataset projections that do not modify source memory. R0.1 storage is **RAM-only**.
8. CPU-first, optional accelerators and Node.js. Do not reveal secrets, network topology or private experiences in this public repository.
9. A synthetic heartbeat and placeholder perception do not establish consciousness, anatomical accuracy or completed AI integration.

## Interfaces planned, not yet implemented

`genesis.genome/1`: inherited/acquired genome; `mechanicus.body/1`: rig, DOFs, proprioception; `mechanicus.physics/1`: model SHA, MuJoCo state/contacts/replay; `mechanicus.neural/1`: cognition and action suggestions; `mechanicus.world/1`: room and external environments; `mechanicus.noosphere/1`: provenance and memory; `mechanicus.simulation-snapshot/1`: atomic revision-consistent checkpoint.

## What R0.1 actually executes

`genesis.seed` → `body.heartbeat` → `sensory.interoception` → `brain.placeholder` → `memory.episode`; `world.room` also feeds `brain.placeholder`. Each is a pure fixture node on a virtual clock. The `brain.placeholder` always reports `UNIMPLEMENTED`; there is no learning, no MuJoCo worker and no Noosphere commit yet.
