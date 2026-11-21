# GENESIS development R0.1 (experimental, issue #154)

`mojum.development` is a **pure, Python stdlib-only synthetic fixture** for three
independently scheduled regions: `cortex`, `cerebellum`, `dentate_gyrus`. Its numbers
are **invented tiny test populations**, not measurements of humans, biological
predictions, subjective age, welfare, or cognitive ability.

- `elapsed_ticks` is an ordering clock; it cannot cause neuron attrition, senescence,
  learning freeze, retirement, or death. No automatic age threshold exists.
- Per-region *experience flags* are the only gates for maturation, neurogenesis,
  synaptogenesis, developmental pruning and learning opportunity counters.
- `newborn` cells are a separate cohort. A cell born in this tick cannot mature in
  the same tick. Immature cells may mature up to each region's target; budgets
  limit cumulative neuron and synapse counts.
- Synthetic `plasticity_milli` is positive before and after structural maturity.
  `learning_events` measures **exposure opportunities only**, not proof that a
  neural network learned. Post-maturity updates never lower this plasticity.
- `senescence_mode` is pinned to `disabled`. An opt-in experimental senescence
  profile is deliberately **not implemented** in R0.1; it cannot be enabled
  inadvertently by restoring a snapshot.
- Snapshots bind the exact program revision and preserve the stable organism ID,
  genome revision, tick and regional counters. SHA-256 detects accidental mismatch
  but **is not authentication or a distributed backup**. The caller still must
  persist/verify actual event inputs and the GENESIS genotype ledger separately.

A basic offline example:

```python
from mojum.development import initial_state, synthetic_program, advance, snapshot, restore

program = synthetic_program()
state = initial_state(program, "example-organism", "genome-ledger-revision")
state = advance(state, program, {"cortex": 1, "cerebellum": 0, "dentate_gyrus": 1})
state = restore(snapshot(state, program), program)
```

Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_development.py' -v`.

## Explicit non-goals and next integration

No neuron graph, actual neural plasticity, aging clock calibration, biologically
faithful counts, learned autonomy, personhood determination, SQLite integration,
recovery across machines, or complete satisfaction of #154 is claimed. A later
reviewed adapter should attach the versioned lifecycle to the real GENESIS
ledger and Noosphere journal with strict replay, add evidence semantics and
per-cell-type/milestone schedules, and compare stimulated vs unstimulated
long-duration runs with measured resource and memory budgets.
