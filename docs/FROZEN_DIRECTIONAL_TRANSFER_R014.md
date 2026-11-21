# R0.14 — frozen directional transfer across unseen synthetic rooms

**Evaluation only.** This test never trains on the held-out scenes or modifies
the actual Aquarium, motor authority, SQLite journal or real device. It evaluates
an opt-in deterministic numeric/bearing memory from experimental PR #90. The
released branch `main` does **not** yet contain this capability.

## Run

```sh
PYTHONPATH=src python -m mojum.bearing_transfer_benchmark
PYTHONPATH=src python -m unittest -v tests/test_bearing_transfer_benchmark.py
```

The first command prints a structured JSON evaluation with each original
prediction, actual post-step measurement and explicit `correct/wrong/abstained`
counts. The evaluation is offline, CPU-only, stdlib-only. There is no external
API, virtual-camera image, MuJoCo body or separate journal.

## Protocol

1. Observe two independent **real synthetic 4cm x+ transitions** generated
   by `ToyScene.sense_direction`, `ToyScene.sense`,
   `world_room.initial_state` and `world_room.advance`. From (1,2),
   the original synthetic toys at (1.22,2) and (.78,2) share numeric
   distance bucket 2, but move to buckets **1** and **2** respectively.
2. Train two frozen empirical models from the **same two observations**:
   `RangeOutcomeMemory` using numeric distance/action only, and
   `BearingOutcomeMemory` using the *measured* coarse bearing in addition.
   Use a separate zero-trained `BearingOutcomeMemory` as a frozen negative.
   Learners do not receive toy identity, RGB/softness, obstacle geometry,
   room coordinates, or future state as features.
3. Evaluate seven **held-out** scenes, including an enlarged room with a
   remote new wall and translated positions, altered reflectance/softness,
   a changed distance inside the same coarse 10cm bucket, a newly blocking
   wall, a novel north-facing direction, and a toy hidden by an actual wall.
   The only evaluated request is a bounded, **4cm** `x+` move. Every actual
   next bucket and blockage is *measured after* `world_room.advance`;
   nothing is inferred from an imagined outcome.
4. **Freeze** the learned snapshots for all held-out predictions. Count a
   prediction as **correct** only when both post-step numeric bucket and
   blocked flag match. A non-abstaining wrong pair counts as **wrong**.
   `blocked=null` means abstention, not success. Correct/attempted and
   coverage are separate; no confidence intervals or statistical claim
   are implied by this small hand-built deterministic sample.

## Explicitly measured outcomes

| Held-out case | Coarse prior bearing/range | Bearing-model result | Why |
| --- | --- | --- | --- |
| translated east in larger room | x+ / 2 | **correct** | same measured next bucket 1 |
| translated west in larger room | x- / 2 | **correct** | different bearing, next bucket 2 |
| unseen RGB and softness | x+ / 2 | **correct** | learned features omit those fields |
| east at farther position, same 10cm bin | x+ / 2 | **wrong** | actual next bin remains 2, predicts 1 |
| west plus new obstacle in movement path | x- / 2 | **wrong** | actual x+ motion blocked; prior predicted free |
| previously unseen north direction | y+ / 2 | **abstains** | no training in that bearing |
| toy completely occluded by new wall | none / none | **abstains** | no visible numeric feature |

If the fixture and code are unchanged, expected **bearing** counts are
`3 correct / 2 wrong / 2 abstained` out of 7. The **numeric-only**
control has an exact 1-to-1 tie from the mirrored training experiences
and abstains on all seven. The **untrained** model likewise abstains on
all seven. These counts are acceptance assertions, not a general estimate
of accuracy. Giving no answer must not be reported as success.

## What this falsifies

Coarse direction is a useful representational feature for **some** translated
synthetic cases, but it is **not sufficient** to learn physics or guarantee
safe action. Same-bucket differences remain unobservable, and a newly placed
wall can invalidate a prediction while preserving the apparent sensor cue.
Unknown cues must abstain; the actual guided runtime may fall back to bounded
exploration and still collide in a new room. There is no physical actuator
to command and no real-world safety guarantee.

This evaluation is distinct from an **end-to-end continuous relocation**:
the experimental Aquarium pins its world and toy scene in the PRIVATE SQLite
bootstrap and intentionally rejects reopening a journal with another scene.
A real transfer workflow would need explicit authenticated state export,
scene-change events, versioned semantics and separate authorization; it is
**not implemented**. Nor does this experiment show a newly trained brain,
consciousness, subjective experience or generalized cognition.
