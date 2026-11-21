"""Run with PYTHONPATH=src python examples/lifecycle_event_ledger_demo.py (stdlib only)."""
import json

from mojum.lifecycle_event_ledger import Lifecycle, PROFILE_SCHEMA


def region(initial):
    return {
        "initial_mature": initial, "initial_immature": 8,
        "newborn_cap": 4, "birth_per_evidence": 1,
        "mature_per_evidence": 3, "synapses_initial": 20,
        "synapses_cap": 80, "synapses_per_learning": 2,
        "prune_per_evidence": 1, "prune_cap": 3,
    }


profile = {
    "schema": PROFILE_SCHEMA,
    "organism_id": "dante-fixture-001",
    "senescence_enabled": False,
    "regions": {
        "cortex": region(20),
        "cerebellum": region(15),
        "dentate_gyrus": region(5),
    },
}
life = Lifecycle(profile)
for name in profile["regions"]:
    life.experience(name, development_units=6, expected_revision=life.revision)

mature = {name: life.region(name) for name in profile["regions"]}
life.advance(1_000_000_000, expected_revision=life.revision)
life.experience("cortex", learning_units=7, expected_revision=life.revision)
restored = Lifecycle.from_snapshot(life.snapshot())

print(json.dumps({
    "classification": "synthetic_lifecycle_not_a_neural_organism",
    "identity": restored.profile["organism_id"],
    "elapsed_ticks": restored.tick,
    "mature_before": mature,
    "after_long_time": {name: restored.region(name) for name in profile["regions"]},
    "snapshot_revision": restored.revision,
}, ensure_ascii=False, indent=2))
