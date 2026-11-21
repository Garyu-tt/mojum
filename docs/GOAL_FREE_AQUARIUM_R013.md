# Canonical Aquarium R0.13 / slice A — goal-free sensorimotor fixture

**Status:** experimental **opt-in** mode. This integrates a small feedback-dependent policy into the **one existing** `mojum.aquarium.Aquarium` organism and its PRIVATE local SQLite Noosphere event stream. It is not full R0.13 acceptance: there is no learned neural brain, vision, tactile softness, MuJoCo, physical safety or semantic object identity. The pre-existing `brain.placeholder` remains explicitly `UNIMPLEMENTED`.

## Run on one CPU, without network or GPU

From this checkout:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante_goal_free.sqlite3 --ticks 120 --exploration-mode goal_free
PYTHONPATH=src python -m mojum.aquarium --db ./dante_goal_free.sqlite3 --ticks 30 --exploration-mode goal_free
```

The same agent/genotype/graph/clock and explicit mode must be supplied on every restart. The CLI rejects explicit nondefault `--dx` / `--dy` in this mode, and the Python API rejects **any** externally supplied `step(dx=...)` / `step(dy=...)`. The default `scripted` mode remains byte-for-byte journal-compatible and retains the original `step()` displacement.

## Causal path, without privileged world data

1. The canonical Graph IR / GENESIS produces the **current tick's** synthetic heartbeat. The current committed world state supplies **only numeric x/y proprioception** and a revision. This policy gets no wall rectangles, environment geometry, toy IDs, softness, true labels, goals, future states or other hidden truth.
2. `Explorer.decide` selects bounded `rest/x+/x-/y+/y-` movement (maximum **4 cm per tick**), with a deterministic rotating innate pattern. A position neighborhood tracks previously measured **blocked** results; actions observed to be blocked there are inhibited. Thus *future actions can differ as a result of experienced collisions* rather than a preloaded target/reward.
3. The existing point-room `world_room.advance` checks its own revision, swept wall collision and virtual tick. The outcome `blocked=True/False` is measured inside that synthetic environment and is fed to a **cloned** policy. This is *not* touch physics, real-world perception or physical motor actuation.
4. A PRIVATE SQLite compare-and-append records **one** frame with the numeric sensor, policy intention, observed collision, learning counters and next policy-state hash, while retaining the existing GENESIS/heartbeat/world/shadow-neural evidence. **Only after the append succeeds** does the runtime adopt the policy, world, shadow model and new tick.
5. On restart, the Aquarium recalculates policy decisions and world outcomes from the canonical event sequence and rejects any non-identical event; no trusting the previous event's `dx/dy` as an external scripted command.

## Versioning and limits

- The new boot manifest pins `exploration_mode=goal_free` and schema `mojum.developmental.explorer/1`. Existing SQLite journals created in `scripted` mode cannot be silently reinterpreted and vice versa.
- The learner snapshot is validated, zone-capped (512), deterministic and inspectable, but its learning signal is simple empirical collision inhibition. It is **not** a reward-trained general motor policy, self-awareness or a model of object identity.
- The physical MotorGate is not invoked for these *synthetic point-room* commands: no physical motor actuation exists. This does not authorize connecting real motors; that would require its own real-body safety gate.
- All data and replay remain local. This does not create encrypted storage, two-host failover or secure agent identity.
- An independent neural / environment causal benchmark, genuine object discovery, witness-backed mesh, privacy assurance and calibrated physics are separate future milestones.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_aquarium_goal_free.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

The new negative controls cover stale action/tick, malformed snapshots, default-mode backward compatibility, rejection of externally scripted displacement, hidden geometry absent from policy inputs, equal-experience deterministic replay, altered measured collision changing a future decision, and SQLite CAS stale-writer rollback. Test evidence and the exact final commit SHA are recorded in the PR.
