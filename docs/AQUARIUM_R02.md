# Mojum R0.2 — offline-first synthetic aquarium integration

This is a **staging integration**, not an independently accepted release of the full GENESIS/Simulacrum project.

## Actual runnable loop

Every simulation tick:
1. The acquired/inherited GENESIS ledger computes a deterministic *synthetic* phenotype at `simTick`.
2. The phenotype heartbeat rate binds into the `genesis.seed` input of the *existing* six-node Universal Graph IR fixture; the original graph runtime produces interoception and a brain placeholder observation.
3. The point-room worker advances the scene by a bounded movement command using exact segment/obstacle intersection (not MuJoCo physics).
4. The complete tick frame, source revisions, interoception, world observation and explicit `UNIMPLEMENTED` brain state are appended to a **real, local, durable SQLite Noosphere** journal using CAS.
5. After SQLite commit succeeds, the in-memory tick/world state is advanced. If the process dies after commit, reopening replays and checks the journal, reconstructing the committed state.

The source GENESIS, world and Noosphere implementations are imported from their original feature/QA branches via Git object history into this **staging-only** branch. Their standalone PRs remain subject to independent acceptance and should not be silently bypassed.

## Run (one command, CPU-only)

From the repository root (no Internet or GPU necessary once checked out):

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120
```

Run the exact command again: the aquarium **resumes** at the next tick rather than overwriting or reinitializing the journal.

Startup checks an existing database before writable initialization. A nonempty
SQLite schema without an events table is rejected, including unrelated tables,
view-only databases and an incomplete journal whose events table was removed.
Such a file is not adopted or repaired, even with `--ticks 0`. New paths, blank
reservation files, schema-empty SQLite files and correctly initialized empty
Noosphere journals still support first-time bootstrap; twin-pair reservations
use that path. Keep journals in a trusted directory: this inspection does not
lock pathnames against replacement by another process.

Reproducible tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Important limitations

- This is **not** a neural substrate, learning system or functional digital organism. The brain always emits `UNIMPLEMENTED`; actions are deterministic external fixture commands.
- There is **no MuJoCo integration or actual physical body** in this loop. Physics is explicitly `NONE_FIXTURE_ONLY` and the point-room returns `physical_contact=false`.
- Each GENESIS phenotype currently rebuilds a separate immutable, synthetic graph revision per step. Proper hot-swap revision barriers and recurrent graph scheduler are future work.
- Recorded timestamps in this fixture are **virtual nanoseconds**, not observed wall-clock times; provenance must be upgraded before mixing with production memories.
- A local SQLite append is atomic, but there is **no cross-device distributed transaction**, encryption, two independent replicas, credential isolation, tamper-proof provenance or end-to-end multi-component atomic checkpoint.
- A malicious writer with filesystem permissions can rewrite the database and its hash chain. Independent externally trusted journal head anchors are optional; never mistake SHA-256 for access control.
- Replay verifies and reconstructs history in bounded SQLite batches (no full-projection load) but still costs CPU time linear in history length and remains capped at 100,000 events. It rebuilds state from scratch, not a scalable memory architecture. GENESIS ledger events cannot rewrite already committed frames. See [streaming replay](STREAMING_REPLAY_R01.md).
- No production VM, Adamantium-node, JediControl, MAS or hardware actuators were modified.
- Staging PR needs independent QA/approval, reconciliation of the competing graph contracts in PR #2 and PR #3 and explicit acceptance of its dependency PRs before release.

## Git provenance / staging

- PR #3 Graph IR first loop `4c26b7c5`.
- PR #9 GENESIS `986eea89`.
- PR #15 point-room swept collision QA `0f6d6bf0` (contains PR #10 ancestry).
- PR #13 Noosphere `2a0c966d`.

This branch is based on a **four-parent integration commit** preserving all these histories. The merge and dependency integration are intentionally not applied to `main` until reviewed and tested.

### Next

Introduce coherent cross-component revision/CAS barriers; replace the brain placeholder with a capability-scoped neural service; then include a real MuJoCo motor/observation loop and replicated Noosphere as independent, acceptance-tested stages.
