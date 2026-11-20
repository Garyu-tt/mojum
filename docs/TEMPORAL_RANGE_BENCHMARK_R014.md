# R0.14 evaluation — measured temporal range transfer (CPU-only)

This is a **separate, deterministic synthetic evaluation**, stacked on the R0.13 direct-main candidate #75. It does **not** change the canonical Aquarium, its journal or any real motor. An independent reviewer must approve the dependency chain before integration.

## Reproduce

```sh
PYTHONPATH=src python -m mojum.temporal_range_benchmark
PYTHONPATH=src python -m unittest discover -s tests -p test_temporal_range_benchmark.py -v
```

Only ordinary Python 3.11+ and existing stdlib code are required; no GPU, Internet, MuJoCo, camera, phone, server or Node-RED. This produces synthetic public evaluation metrics and no PRIVATE organism trace.

## Causal protocol

The benchmark uses **actual** `ToyScene.sense` readings, sampled before and after an **actual** `world_room.advance` displacement. A tiny bounded `TemporalRangeModel` learns frequency counts of observed transitions `(10-cm range bucket, signed 10-cm X step) → subsequent observed bucket`. Its input contains **no RGB value, toy ID, true softness, future distance, object center or obstacle geometry**. Omission of RGB is an explicit inductive bias, *not learned color invariance*. The transition model supports exact snapshot validation, an 128-evidence budget, fail-closed stale tick/forged prior rejection, and no prediction for unobserved contexts.

Training uses three observed 10-cm approach steps toward a red synthetic toy at x=1.2m, from x=0.8m (bucket sequence `3→2→1→0`). The model learns 3 numeric successor transitions. A frozen version observes the same cues but never updates, so its native prediction is **None**. A separately named **persistence** comparator simply predicts that the last bucket remains unchanged. Do not conflate this numeric baseline with the frozen model's native output.

## Held-out results

| Test | Learned prediction | Measured outcome | Learned squared error | Fixed persistence squared error |
| --- | --- | --- | ---: | ---: |
| New green RGB, new x=2.8m toy location, three familiar range buckets | `2, 1, 0` | `2, 1, 0` | `0, 0, 0` | `1, 1, 1` |
| New orange RGB, new x=3.2m location, unfamiliar first bucket | `None, 2, 1` | `3, 2, 1` | `N/A, 0, 0` | `1, 1, 1` |
| Previously unseen wall blocks the attempted movement | `2` | `3` | `1` | `0` |

The green example proves *narrow within-fixture numeric transition transfer* to a new toy color and absolute location for an already-observed range trajectory. It **does not** prove object/material understanding or out-of-domain locomotion. The orange case demonstrates honest **no coverage** for a previously unseen range state, and the wall case demonstrates **negative transfer** when invisible geometry violates the learned transition. Neither negative case is omitted from the published outcome.

### Important caveats

- The 10-cm evaluation displacement is deliberately **not** the canonical Aquarium's bounded 4-cm developmental motor action. It is a synthetic test-only actuation in `world_room.advance`, not a suggested physical motor command.
- Synthetic range buckets represent one toy's nearest-point proximity; there is no real RGB camera, depth sensor, material pressure probe, noisy measurement or moving-objects model in this benchmark.
- The model learns a *lookup table*, not a causal physics law; its generalization beyond exact observed `(range, motion)` contexts is **zero by design**. A single selected trace gives no statistical confidence interval.
- No machine-learning claim about wellbeing, suffering, consciousness or an independent organism follows from these tests.
- No user-device, Android, continuous phone hosting or production workloads are touched.

Next meaningful test: measure bidirectional/lateral ranges across randomized held-out scene seeds; compare against a frozen and shuffled-outcome trained control; report coverage, error and uncertainty, not just positive examples. For any genuine Aquarium integration, require typed time-aligned sensor events, authenticated action outcomes, CAS-before-RAM adoption, replay determinism and versioned modes.
