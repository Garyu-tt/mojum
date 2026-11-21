# R0.13 canonical local Aquarium — candidate acceptance (2026-11-21)

> **Candidate only.** This file records the scope of an unmerged opt-in CPU-first
> synthetic fixture. It does not describe the shipped default branch as a
> neural or embodied organism. GitHub CI is evidence, not independent approval.

## One organism / one journal

The canonical `mojum.aquarium.Aquarium` maintains one private local Noosphere
SQLite event journal with the synthetic GENESIS heartbeat, a bounded point-room
world, motor play, optional numeric toy proximity/contact probes and exact replay
on restart. There is no mandatory network, cloud, GPU or Node-RED runtime.

The default `scripted` path and its journal schema remain compatible with the
existing fixture. Opt in to both exploratory movement and toy-contact sensing:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante-experimental.sqlite3 --ticks 120 \
  --exploration-mode goal_free --sensory-mode toy_probe
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante-experimental.sqlite3 --ticks 30 \
  --exploration-mode goal_free --sensory-mode toy_probe
```

The second invocation **must** reuse the same mode, seed, room and graph to
replay the same local history. Do not publish the PRIVATE journal, CLI --watch
trace or unredacted checkpoint. Use a separate file from the old scripted mode.

## Acceptance evidence required from this exact candidate SHA

1. Entire stdlib test discovery on Python 3.11, 3.12 and 3.13; motor and mesh QA
   workflows on all three versions, with no skipped failing jobs.
2. A/B actual point-room collision experiments: equal prior experience gives
   equal actions; differing measured collisions yield later action divergence.
3. Real numeric toy-probe evidence changes *later* probe decisions and
   deflection predictions, and a frozen/no-learning control stays unchanged.
4. Reopen/replay recreates exactly the world, movement policy, contact memory,
   neural shadow predictor and chain head from the *one* private journal.
5. Corrupt or mismatched mode/seed/scene, malformed numeric cues, explicit
   external goal-free displacement, huge numbers, stale CAS and a changed
   shared journal head all fail closed.
6. A second writer must never cause a checkpoint with this process's old
   world or learned memory and the other process's new journal head. Reopen is
   necessary to recover a consistent local state.
7. Regression for hidden spectator toy names: names may affect provenance in
   the experiment's private bootstrap, **not** near-field sensory values,
   measured contact or acquired evidence.
8. Canonical motor-room wall obstacles hide toys behind a **2D center-ray**
   occlusion mask: no hidden object's numeric RGB/range or virtual probe
   should leak through an impassable wall; the next unoccluded toy can still
   be observed and the mask's version is pinned in PRIVATE bootstrap metadata.

## Interpretation and negative controls

The learner is a deterministic small empirical policy, **not** a trained
general-purpose brain. It suppresses previously blocked synthetic actions
and learns cue-specific numerical contact responses. For color-matched
held-out toy IDs it can reuse a memorized RGB response; it **cannot infer**
unseen RGB softness from nothing. If a new toy shares trained RGB but has a
different hidden material response, negative transfer can be **worse** than a
simple numeric prior. `python -m mojum.sensory_benchmark` prints explicit
frozen and held-out synthetic counterfactuals, including absence of a
prediction for truly unseen cues.

The occlusion mask is a deliberately simple **line from observer to
toy center**, not calibrated optics, object silhouette tracing, reflection,
depth mapping or actual collision/contact. The opt-in toy-probe journal now
pins `occlusion_schema=mojum.sensor.center-ray-wall-mask/1`. Earlier
experimental toy-probe SQLite journals without that contract intentionally
fail to reopen under the revised sensor; use a separate database path rather
than silently reinterpreting previously committed experience. The historical
default scripted journal schema is unchanged.

The `brain.placeholder` remains `UNIMPLEMENTED`; no camera perception,
MuJoCo calibrated physical body, real motor or tactile actuation, emotion,
consciousness, encrypted distributed Noosphere, cross-host consensus or
Android background runtime has been demonstrated. Release acceptance for a
full digital organism is therefore **BLOCKED** even if all these tests pass.

## Provenance and review dependencies

This candidate unifies source work from #59, #61, #63, #65, #69, #70 and
evaluation-only tests from #73, after #33/#56 sensory contract fixes.
The staged source has been integration tested as #72; the RC exists solely
to evaluate a **direct main-target diff**. #68 is a separate experimental
four-ray modality; #58/#62/#67/#71 concern distinct local freeze/authority
and lineage protocols. None is silently enabled here.

Require a separate human/account-distinct reviewer and successful GitHub
Actions on the **exact final RC head** before considering any merge. A shared
GitHub account operated by multiple AI agents is not independent review.
Do not merge an intermediate stacked PR directly into main. Do not close
#55 or #51 based only on this candidate.
