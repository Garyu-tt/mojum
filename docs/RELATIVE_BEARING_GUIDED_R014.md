# Mojum R0.14 — opt-in relative bearing, not a location oracle

This is an **experimental CPU-first synthetic observation and motor mode**, stacked on [#90](https://github.com/Garyu-tt/mojum/pull/90). It is not a release of a general neural brain, real camera, physical motor, MuJoCo body, or distributed/Android organism.

## Problem and measured counterfactual

At position `(1.00,2.00)`, a virtual toy center `(1.22,2.00)` and another at `(0.78,2.00)` both produce the coarse numeric **10cm range bucket 2**. A **real virtual 4cm x+ step** changes the next range bucket to **1** for the right-side toy and remains **2** for the left-side toy. R0.14's previous `range_guided` model conditions only on `(range bucket, action)`; both examples therefore collide in one histogram. The negative-control model correctly abstains on ties but cannot distinguish those scenes.

This experiment adds a **sensor output**, not an object-coordinate or privileged world-state input: `bearing_bin` ∈ `x+`, `x-`, `y+`, `y-`, `center`, or `null` if no visible toy. The sensor chooses the closest unoccluded toy; it masks toys behind solid room walls using the existing coarse 2D center ray. A relative direction is expressed in **fixed world axes**, not in body-relative orientation (the fixture has no heading). Directional sensing does not reveal toy ID, exact coordinates, hidden softness, obstacle coordinates, or the future world step.

## Two explicit separate modes

- `--temporal-mode range_guided` — old R0.14 number-only behavior, **unchanged**; a conflicting 10cm context may abstain and fall back to ordinary exploration.
- `--temporal-mode bearing_guided` — new optional bearing-conditioned empirical movement predictions. It reuses the exact tested range/outcome histogram model **per observable bearing** rather than inventing a separate physics engine. The current visible direction and range are sampled before action; a measured post-action cue and blocked result update the model **only after the single PRIVATE SQLite CAS commit**. The controller still only proposes 4cm-or-rest synthetic movement. Unknown contexts remain eligible, and contradictory outcomes **within a bearing** still abstain.

The default `--temporal-mode none` and `range_outcome` remain available and unchanged. The existing contact/softness association stays separate and is not used for motor choice.

Run in a **new local private SQLite file** from the source checkout:

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./dante-bearing.private.sqlite3 \
  --ticks 40 --neural-mode disabled --exploration-mode goal_free \
  --sensory-mode toy_probe --temporal-mode bearing_guided
PYTHONPATH=src python -m mojum.aquarium --db ./dante-bearing.private.sqlite3 \
  --ticks 40 --neural-mode disabled --exploration-mode goal_free \
  --sensory-mode toy_probe --temporal-mode bearing_guided
```

The second command must reconstruct the exact previous world, empirical model, chosen actions and local journal head. Do **not** upload the PRIVATE database or `--watch` JSONL traces. SQLite is not encrypted; hashes are not authentication.

## Falsification / acceptance gates

1. Two mirrored toy centers yield the **same numeric distance bucket** but opposite bearing, and a real `world_room.advance` produces different outcomes.
2. Each observed bearing/action learns a separate prediction; a prior learned for `x+` must not count as evidence for `x-` even if the color, distance bucket, or action is otherwise identical.
3. A toy behind an impassable wall leaks **no** RGB, direction, range or virtual contact; occluded cues have a null bearing.
4. Exactly one local SQLite journal holds the measured transition and is deterministically replayed on restart; failed/stale CAS cannot adopt uncommitted motor or bearing memory.
5. The new `mojum.temporal.relative-bearing-outcome/1` model and `mojum.sensor.synthetic-near-field-bearing/1` sensor contract are pinned in the bootstrap. The prior range model `mojum.temporal.range-outcome/2`, old R0.13 sensor/scene and legacy modes retain their original schemas and refuse reinterpretation of a different journal.
6. Tests across Python 3.11/3.12/3.13 in Aquarium, Motor Gate and Mesh CI plus independent account-distinct review before any actual release.

This adds an **engineer-designed directional feature**, not spontaneously acquired spatial concepts. 5 coarse sectors lose a lot of geometry; identical bearing and range can still hide obstacles, different trajectories and contact outcomes. A majority vote is not calibrated certainty, and the policy has no real embodiment or autonomous neural generalization.
