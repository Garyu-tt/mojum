# R0.1: opt-in GENESIS development clock, no age decay

This **standalone, synthetic, CPU-only** contract separates three counters:

- `elapsed_ticks`: how long the fixture has been running in its own virtual-time accounting;
- `development_ticks`: independent paced developmental exposure, used to derive a visual/developmental age, **clamped at maturity**;
- `learning_events`: a counter of externally reported learning activity, **not** evidence of a functional brain or real learning.

Only the isolated `mojum.development_clock` functions implement this contract; existing `mojum.development` and `mojum.lifecycle` modules in main retain their public APIs. No changes
are made to `Aquarium`, existing `GenomeLedger`, Noosphere SQLite history,
checkpoint hashes, private training records, physical models or real organism
semantics. This is not wired into the default simulation. Callers must persist
state using their own validated append/CAS transaction; an uncommitted result
from `advance` is not an authoritative checkpoint.

## Example: growth from virtual age 6 through 16

From the repo root, use Python 3.11+ with `PYTHONPATH=src`:

```python
from mojum.development_clock import default_policy, initial_state, advance, project

policy = default_policy()
policy.update(start_age_milli=6000, maturity_age_milli=16000,
              ticks_per_virtual_year=10)
state = advance(initial_state(), elapsed_ticks=25,
                development_ticks=100, learning_events=3)
print(project(state, policy))
# elapsed_virtual_years_milli = 2500 (2.5 elapsed years)
# development_age_milli = 16000 (mature, 16 years)
# learning_events = 3 (counter only)

state = advance(state, elapsed_ticks=1000, development_ticks=1000,
                learning_events=200)
assert project(state, policy)["development_age_milli"] == 16000
assert project(state, policy)["learning_events"] == 203
```

`_milli` fields are integer thousandths of a virtual year. All calculations are
integer-only and deterministic; policy and state schemas reject unknown fields,
float/boolean counters, negative values, counter overflow, and any attempt to
set `senescence_enabled=True`. There is no aging-related death or age-related
decay in this fixture, regardless of elapsed time. The two clocks can run at
different rates by caller choice; age does not silently advance from wall time.
This does **not** guarantee avoidance of suffering, nor simulate a biological
aging mechanism, cognition, developmental learning or real neural plasticity.

## QA

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_development.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Future integration should pin policy identity and state to the same replayable
journal CAS boundary as the rest of the organism, separate developmental
morphology from neural learning, and add independent reviewer sign-off before
switching away from the synthetic fixture.
