# R0.13 frozen-model and held-out toy benchmark

**CPU-only synthetic experiment, not a brain or cognition benchmark.** This adds a pure, deterministic measurement module. It does not modify the canonical Aquarium, its private Noosphere SQLite journal, learned policy, real hardware, or parent PR #70.

## Reproduce

`PYTHONPATH=src python -m mojum.sensory_benchmark`

`PYTHONPATH=src python -m unittest discover -s tests -p test_frozen_heldout_sensory.py -v`

The benchmark produces evaluation-only JSON (not a private organism trace).

## Experimental protocol

The bench reuses the real synthetic `ToyScene.sense`, `ToyScene.probe` and `ContactAssociations` from R0.9 / canonical R0.13. The evaluator positions a toy at (1.20, 2.00) and a read-only contact instrument at (1.00, 2.00). **Before the outcome is measured**, the learner receives only numeric RGB/range cues and their tick/revision. Toy ID and hidden softness are experimenter-only and never supplied to `predict`.

Training uses two colors (red response .75 and blue response .20), with two measured probe events for each color. The active model updates four times. The **frozen** `ContactAssociations` model sees exactly the same numeric cues but is never updated: its own prediction remains `None`. For a numerical squared-error comparator we separately use a **fixed 0.50 prior**—this is deliberately *not* attributed to the frozen model.

Held-out probes are made on four **new toy IDs**, none used for training. Their true deflections are revealed only to the evaluation score, after predictions. Neither model is updated during the test.

| New toy condition | Active prediction | Measured outcome | Active squared error | Fixed-prior squared error |
| --- | ---: | ---: | ---: | ---: |
| Known red RGB, same response | .75 | .75 | 0 | .0625 |
| Known blue RGB, same response | .20 | .20 | 0 | .09 |
| Known red RGB, contradictory response | .75 | .10 | .4225 | .16 |
| **Novel green RGB** | **None** | .40 | **not scored** | .01 |

## Interpretation and limitations

The memory **does** transfer to an unseen *toy ID* when its RGB and response equal a trained sample. But this is exact-cue reuse, **not** novel-feature generalization. The contradictory red case is a clear **negative transfer**: its trained prediction is worse than the naive 0.50 prior. The novel green case has **zero prediction coverage**, not a successful unknown-material inference.

This is a tiny, deliberately fixed four-case negative-control fixture, not a statistically representative performance estimate. Any future claim about actual object recognition needs multiple held-out scene seeds, confidence bounds, physically observable features beyond RGB, a shuffled-label trained control, and a model that can honestly express uncertainty about unseen cues. No real camera, material recognition, physical contact, MuJoCo, motor authority, Android daemon or distributed continuity is implemented here.

## Dependency order

Stacked over #70, which is stacked over canonical #65, above #59; source reuse and QA dependencies also include #33/#56 and input-boundary/checkpoint QA #61/#63/#69. Do not merge directly into `main` before upstream acceptance and independently reviewed final-head CI.
