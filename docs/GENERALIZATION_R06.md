# Mojum R0.6 — held-out room generalization and negative controls

**QA staging only.** This protocol depends on R0.5 PR #27, which depends on
R0.4 PR #26. Do not merge any stacked branch without independent review.

## Hypothesis

R0.5 can learn tabular value estimates from point-room collision/progress
feedback, but the earlier **single-room** result (116 versus 139 ticks)
does not establish transferable navigation. This R0.6 exercise measures
*what happens in genuinely different room layouts*, including known
unreachable goals and physically reachable goals requiring a temporary
detour away from the target.

The evaluation script deliberately does **not change the policy**. Any
improvement or failure is evidence about the existing limited controller
under this controlled fixture, not about a production neural brain.

## Fixed, non-cherry-picked experiment

Training:
- One 4 x 4 m room, one local SQLite journal, 160 ticks on R0.5
  `learned_fixture`, with the original wall and goal at (3.5,3.5).
- Only the *policy snapshot* transfers to hold-out episodes; no
  Noosphere episodes, memory contents or raw target-room geometry are
  loaded into the training loop.
- Every evaluation starts from (1,2) and the same target; goals and
  room geometry are fixed by this script.

Held-out scenes:
- `open_floor`: obstacle-free easy control.
- `shifted_barrier`: new wall at a different X coordinate.
- `upper_barrier`: another shifted wall with a different gap.
- `detour_required`: a fully *reachable* goal, but its path must go to
  Y=3.9 before returning to Y=3.5. The existing X/Y-only,
  goal-directed controller **cannot** propose that necessary detour.
- `sealed_barrier`: a wall spanning the room height, deliberately
  unreachable from the start; no method should fake success.

For each room, seed and horizon, compare:
1. `reactive`: deterministic R0.4 non-learning policy.
2. `scratch_online`: fresh R0.5 table learning on the test room.
3. `transfer_frozen`: R0.5 table trained in the original room; no
   Q-value updates allowed in the held-out room (zero-shot transfer).
4. `transfer_online`: same pretrained table with new test-room rewards
   enabled (adaptation).

Evaluation seeds (1979, 1981, 1983, 1985) reinitialize the **exploration
PRNG state only**, not the learned Q values. All variants run the same
synthetic graph heartbeat through the same Virtual Graph IR executor and
the same deterministic swept-collision worker. Results include per-seed
reaching time, blocked count, final Manhattan goal distance and number
of online Q-updates. Means and medians are descriptive only; do not
turn 3-4 fixed seeds into a statistical significance claim.

## How to run

```bash
PYTHONPATH=src python examples/evaluate_transfer_r06.py --ticks 240 --train-ticks 160 --seeds 3
PYTHONPATH=src python -m unittest tests/test_transfer_r06.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

No GPU, MuJoCo, real-world sensor, hardware actuator, internet service
or access to another user's Noosphere is used. The 4 x 4 m space is an
inert point-room fixture rather than a full body.

## Interpretation and acceptance

**Pass conditions for QA tooling**, not claims of learning quality:

- No target room has exactly the training obstacle geometry.
- The result is deterministic across identical runs, with explicit
  seeds and per-case metrics.
- Pretrained `transfer_frozen` Q-values never change; the training
  snapshot is never mutated by evaluation.
- A manual waypoint witness proves that the `detour_required` scene is
  physically traversable. Neither R0.4 nor R0.5 may be credited with
  success unless it actually reaches the goal using permitted actions.
- The sealed negative control must never report success.
- Comparisons may show worse held-out results for transferred policies:
  publish those outcomes rather than silently replacing the rooms.

**Architectural consequence:** If learned values don't transfer, add
senses, explicit spatial reasoning, relative geometry and safe movement
away from the goal *before* increasing network size. Learning Q values
in absolute (X,Y) cells is not enough to generalize topology.
