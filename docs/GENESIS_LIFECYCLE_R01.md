# GENESIS lifecycle R0.1 — opt-in synthetic maturation

This isolated stdlib component in `mojum.lifecycle_ledger` is a candidate toward issue #154, **not** the live brain or a biological prediction. It does **not** change the released Aquarium SQLite schema, historical events, or default runtime. No new body, neuron backend, intelligence, subjective experience or welfare claim is made.

## Contract

- Profile `mojum.lifecycle.profile/1` pins one immutable organism ID and 1–16 separately parameterized region populations. The three-region demo uses `cortex`, `cerebellum`, `dentate_gyrus`; numeric constants are artificial test values, not human brain measurements.
- `elapsed_ticks` is independent from `development_progress` and `learning_progress`. Advancing ticks alone cannot mature neurons, prune synapses, cause attrition, death or shut down learning. Structural maturation is gated by explicitly recorded synthetic development evidence, not a real biological developmental program.
- Mature/immature/newborn neuron counters are bounded and deterministic. Newborn counts are separately observable. The synthetic synapse counter can change after structural maturity via independently recorded learning units, subject to a fixed capacity. Counters are **not** evidence of acquired skill, memory retention or plastic neural weights.
- Senescence is explicitly `false` and `true` is rejected for this schema: a future experimental ageing model requires a separate versioned contract; old profiles cannot silently opt in.
- The append-only event list (max 4096) records virtual-time advancement and voluntary practice separately. CAS hash revision prevents accidental stale writes. `mojum.lifecycle.snapshot/1` reconstructs and revalidates every event and the final tick and checksum. It is **not authenticated**, encrypted, or independently durable.
- Total virtual ticks are bounded at 2^63−1; per-region counters at 10,000,000 units; no mandatory food, water, imposed grades, adverse states, or physical actuation.

## Try it offline

```sh
PYTHONPATH=src python examples/lifecycle_demo.py
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lifecycle*.py' -v
```

The demo artificially matures three regions, advances a billion virtual ticks with **no age-triggered decline**, then records extra learning units for cortex and replays the snapshot. This demonstrates **contract behavior only**.

The existing canonical `mojum.lifecycle` and `mojum.development` modules remain untouched. This is an independent event-ledger experiment, not a drop-in replacement for either public API.\n\n## Before integrating with Aquarium

Require independent QA on this PR, strict main-head CI and explicit versioned bootstrap/journal migration design. To satisfy #154 fully, still need genotype-driven per-cell trajectories, evidence provenance rather than synthetic unit counts, nontrivial synaptic learning/retention tasks, birth/migration/apoptosis model calibration, private multi-copy checkpoints and replay in the canonical organism runtime. Do not present this proposal as a finished continuous life.
