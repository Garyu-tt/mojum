# Neural population LOD R01

mojum.neural_lod provides an opt-in, deterministic resource-budget allocator for explicitly supplied PUBLIC_SYNTHETIC population descriptors. It assigns each population a caller-defined coarse, medium, or fine representation tier. It does not simulate neurons, change neural state, schedule conduction events, or select a biological model.

## Example

```python
from mojum.neural_lod import CLASSIFICATION, plan_lod

populations = [
    {
        "population_id": "motor-left",
        "importance_milli": 800,
        "coarse_cost_units": 10,
        "medium_cost_units": 20,
        "fine_cost_units": 40,
    },
    {
        "population_id": "sensory-right",
        "importance_milli": 500,
        "coarse_cost_units": 8,
        "medium_cost_units": 14,
        "fine_cost_units": 30,
    },
]

plan = plan_lod(
    populations,
    budget_units=36,
    classification=CLASSIFICATION,
    consent=True,
)
print([(item.population_id, item.level) for item in plan.populations])
```

## Admission and allocation

- Exact native Python list/dict/string/integer/bool values are required; bool is never accepted as an integer.
- Every population ID is a unique restricted-ASCII identifier.
- Caller supplies strictly increasing positive costs for all three tiers and a bounded nonnegative importance score.
- The budget must fund the coarse baseline for every population; otherwise the request is rejected without a partial plan.
- The allocator repeatedly chooses the affordable next upgrade with the highest importance_milli / incremental_cost ratio. Ratios use integer cross-multiplication, never floating point. Ties resolve by population ID, then tier.
- The policy is a bounded deterministic greedy heuristic, not a globally optimal knapsack solver. Callers should not infer optimality from a valid receipt.
- Output populations are sorted by ID and returned as frozen records. The SHA-256 digest covers the complete decision and accounting fields; it is a consistency checksum, not authentication.

## Limits and non-goals

At most 128 populations, 1,000,000 budget units, 100,000 cost units per tier, and 1,000,000 importance milli-units per population. Selection performs at most 256 upgrade rounds over at most 128 rows. There is no file/network access, clock, third-party dependency, GPU call, training, neural dynamics, hidden state, physical actuation, or hardware integration.

Tier costs and importance are caller estimates in arbitrary integer units. The module makes no biological-fidelity, accuracy, energy, latency, or equivalence claim about any population representation. It is an allocation receipt only and does not modify mojum.neural or mojum.spinal_conduction.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neural_lod.py' -v
```
