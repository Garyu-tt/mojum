# Creative school R0.2 — scripted offline art + rhythm fixtures

Stacked on school catalog R0.1 PR #165; **experimental / not shipped in main**.
This Python-stdlib runner does not simulate an autonomous teacher, learned
neural policy, audio synthesis, physical movement, Noosphere writes or consciousness.
It accepts an explicitly selected `visual_arts.starter` or `music.starter`
and five scripted steps in the order `observe → experiment → make → revise → reflect`.

It returns two inspectable PRIVATE artifacts (SVG paths or JSON pulse indices),
five PRIVATE step records containing `sim_tick`, activity identity, provenance
and an artifact SHA-256 where appropriate, plus feedback indicating whether the
artifact changed. This feedback does **not** assign grades or claim learning.
The separate `compare_trials()` compares a baseline with a later repeat for the
same fixture; transfer is always flagged as *not measured*.

Example from repo root, with R0.1 and R0.2 changes checked out:

```python
from mojum.school_activity import load_catalog, run_activity, redacted_summary
catalog = load_catalog("examples/school_catalog_r01.json")
script = {
    "observe": "simple line", "experiment": "try a different endpoint",
    "make": {"strokes": [{"x1": 2, "y1": 2, "x2": 12, "y2": 12}]},
    "revise": {"strokes": [{"x1": 2, "y1": 2, "x2": 16, "y2": 12}]},
    "reflect": "the line is longer",
}
private_run = run_activity(catalog, "visual_arts.starter", script, sim_tick=0)
svg = private_run["artifacts"][1]["content"]
public_info = redacted_summary(private_run)
```

Neither `private_run` nor `svg` is automatically written to disk or exported.
The explicit `redacted_summary` projection reveals only activity and aggregate
counts, not private notes, learner ID, event history or artifact content.
Resource limits: 64 sections, 128 KiB catalog, at most 32 strokes/pulses, 64x64
grid, 16 KiB artifact, and a five-tick bounded run. No network/GPU/audio access.

QA:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_*.py' -v
```

Real educational progress and cross-subject transfer need later controlled
trials against independent baselines, not deterministic scripts alone.
