# Mojum R0.3 — first real online-learning circuit (shadow mode)

Mojum is the entire GENESIS → brain → Dante body → physics/world → Noosphere
simulation. This PR is a **small neural-substrate increment**, not a complete
brain or functioning digital organism.

## Actual behavior

`PulsePredictor` is a 3-feature, bounded CPU-only online least-mean-squares
learner. It is self-supervised by a synthetic internal heartbeat. At logical
tick `t`, it estimates `pulse_t` from **only tick `t−1` inputs**:
a bias, the prior heartbeat phase in thousandths, and the prior pulse bit.
Only after observing `pulse_t` does it change its three weights using the
prediction error. It emits a probability-like clamped score and a
squared-error metric. No NumPy, LLM service, GPU or network access is required.

**Learning is real and observable:** weights change in response to labels,
errors on a constant absent-pulse stream decrease with training, and identical
experience sequences result in identical snapshots. This alone establishes
nothing about understanding, generalization, consciousness, sensory grounding
or biological cognition. A predictable oscillator is only a test fixture.

The `brain.placeholder` graph node still returns `UNIMPLEMENTED`. The
new module appears separately as `learning_observer` in each aquarium
frame. `motor_intention` is always `null`; the learner cannot actuate
motors. The active motor-safety gate and full MuJoCo body are **not integrated**.

## Replay, revisions and memory

The source-of-truth is the same **local SQLite Noosphere** journal from PR
#13, assembled in staging PR #19. Before a tick is committed, Mojum clones
the learner state, calculates a delayed update and includes the resulting
observations in the frame payload. Only after the journal accepts the tick
via CAS does it replace the in-memory learner. A process crash after a
successful append but before updating RAM is recovered by deterministic
replay of all committed frames, including the same learning updates.

The bootstrap manifest pins `neural_mode` and
`neural_model_schema=mojum.neural.delayed-pulse/1`; a database created in
`disabled` mode cannot be reopened as `shadow` without explicit migration.
Likewise, earlier R0.2 journals must be migrated, not silently reused as
R0.3. Neural snapshots have bounded parameters and structure checks.
Every private event has source/revision/time provenance, while the public
Noosphere export excludes it by default.

No cross-host distributed transactions, durable model snapshots separate
from the journal, verified encryption, signed identity, protected private
memories or trained inference model exist yet. Replay of the source journal
remains limited by R0.2's 10,000-event cap.

## Run from repository checkout

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_r03.sqlite3 --ticks 120 --neural-mode shadow
PYTHONPATH=src python -m mojum.aquarium --db ./dante_r03.sqlite3 --ticks 30 --neural-mode shadow
PYTHONPATH=src python -m unittest discover -s tests -v
```

For a fixture without learning, create a **different database** and select
`--neural-mode disabled`. The command uses the same simulated 10 ms tick,
point-room, inherited GENESIS genome, placeholder brain, and synthetic
heartbeat; no hardware is accessed.

## Scope, review and next experiments

This draft PR is **stacked onto PR #19**, not main. A separate peer must
review both upstream integration and learning behavior before merging.
Follow-on tasks: CPU-first persistent event prediction on richer sensory
streams, a replaceable policy model with strictly capability-scoped outputs,
shared simulation-clock interface for MuJoCo and controller feedback, and
privacy-safe multi-node storage. Never claim these as R0.3 achievements.
