# R0.14 opt-in measured numeric sensor → virtual motor guidance

This is a **CPU-only synthetic experiment on the canonical Aquarium**, not a
general-purpose brain, autonomous body or real sensor. It is stacked over
R0.13; it has not been merged into `main`. Physical motors remain unavailable.

## Two distinct experimental modes

- `--temporal-mode range_outcome`: **observation-only** memory from the actual
  canonical 4cm developmental action. Each PRIVATE SQLite `aquarium.frame`
  stores the pre-action near-field range bucket, intended bounded action,
  actual post-action range and measured blocked outcome. This is the prior
  #84 measured-outcome mechanism, modified to honor R0.13's pinned wall mask.
  The original Explorer continues to choose actions without this memory.
- `--temporal-mode range_guided`: **the same bounded memory influences the
  actual subsequent action**. Before the action, the organism reads its own
  current numeric near-field cue and past `RangeOutcomeMemory` outcomes only.
  It vetoes actions previously predicted blocked for that cue; among remaining
  allowed actions it tentatively prefers known unblocked actions predicted to
  change the range bucket. Otherwise it follows the existing rotating
  exploratory repertoire, with safe synthetic rest as the fallback.

This is an *explicitly designed intrinsic novelty heuristic*, not a learned
general policy or an evolved need for an object. Both outcomes are imperfect
in unfamiliar rooms; one past blocked/unblocked measurement is not a robust
confidence estimate. Unknown actions remain available for exploration.
No RGB/object ID/hidden softness, obstacle coordinates or future measurements
enter the motor-guidance table.

## Run — independent private SQLite journals per mode

From the repository root with Python 3.11+:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./r014-guided.private.sqlite3 --ticks 40 \
  --exploration-mode goal_free --sensory-mode toy_probe \
  --temporal-mode range_guided --neural-mode disabled
PYTHONPATH=src python -m mojum.aquarium \
  --db ./r014-guided.private.sqlite3 --ticks 40 \
  --exploration-mode goal_free --sensory-mode toy_probe \
  --temporal-mode range_guided --neural-mode disabled
```

For a **non-guided trained memory control**, choose a *different* private DB and
use `--temporal-mode range_outcome`. For the R0.13 baseline, omit
`--temporal-mode`. Do not change the temporal/sensory/exploration modes of an
existing journal: their schemas and wall mask are pinned in bootstrap and
replay deliberately rejects reinterpretation. `--watch` emits **PRIVATE**
synthetic JSONL; do not upload the SQLite file or traces publicly.

## What a frame actually proves

- `developmental.intent.action`: the concrete intended 4cm-or-rest movement.
- `motor_guidance.observation`: current low-level numeric RGB/range cue;
  `motor_guidance.predictions`: historical **numeric range/blocked**
  forecasts for the five bounded actions. `avoid` and `prefer` contain the
  current empirical decision constraints. This field exists **only** in
  `range_guided`.
- `temporal_range`: action, requested vs *actual* displacement, preceding
  and following **wall-masked** sensor cues, forecast and actual blocked
  result, and post-learning model checksum. Both temporal modes use this
  field and update from measured outcomes **after** the SQLite append.
- `toy_contact`: prior opt-in numeric virtual probe and empirical material
  deflection table. Its measured softness prediction **does not yet direct
  motor selection**. Neither a toy identity nor a true material label is an
  action input.

All events belong to ONE PRIVATE local Noosphere SQLite CAS journal. Upon
reopen, deterministic replay reconstructs the movement policy, sensory
contact association, temporal range memory, world and adopted head. A stale
second writer or failed event append cannot silently adopt a different
guidance state as a valid checkpoint.

## Adversarial acceptance evidence

- Actual synthetic `world_room.advance` measured blocking leads to a
  different future motor intention under the guided policy, compared against
  the original Explorer using the **same** pre-action self-signal.
- A second real synthetic step whose 10cm range bucket changes after a 4cm
  move generates an empirical preference not available at birth.
- **End-to-end** test must observe at least one motor action changed by the
  organism's own **committed journal history**, without externally injected
  model state or scripted displacement, then verify exact cold restart and
  identical next frame.
- Spectator toy IDs, RGB and hidden material softness changes do not
  affect range-based movement given identical geometry; invalid mode,
  forged motor guidance, writer conflict and wrong replay schema fail closed.
- Full GitHub Actions stdlib Aquarium, Motor Gate and Mesh Contract tests
  on Python 3.11, 3.12, 3.13 at the exact final SHA.

## Boundaries and upstream

Reuses the actual temporal experiment and private state validator from
[#84](https://github.com/Garyu-tt/mojum/pull/84), the measured temporal
benchmark from [#79](https://github.com/Garyu-tt/mojum/pull/79), and the
occlusion-safe canonical R0.13 release candidate
[#75](https://github.com/Garyu-tt/mojum/pull/75). The code is in an
independent branch; it does not edit those peers' working branches or main.

No real camera/touch, MuJoCo physics, embodied forces, Android background
service, trusted distributed brain transfer, trained general neural network,
objective-free emergent cognition, or demonstrated consciousness is present.
The primitive numeric model can wrongly extrapolate in a new room and does not
learn the representation itself. `brain.placeholder` remains UNIMPLEMENTED.
PR #75 and #84 must be reviewed/reconciled independently before release.
A green stacked CI is not account-distinct independent approval.
