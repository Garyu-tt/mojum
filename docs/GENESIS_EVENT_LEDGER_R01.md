# GENESIS event ledger R0.1: isolated experimental contract

This PR deliberately preserves the accepted `mojum.lifecycle.DevelopmentClock` and `mojum.development` APIs. It isolates the alternative event-ledger prototype from PR #173 under `mojum.lifecycle_event_ledger`. It is NOT a migration or alias for either accepted API, and it does not change the default Aquarium runtime, the Noosphere database, real journals, bodies, or neural activity.

## Scope and trust

- `Lifecycle` is an opt-in bounded synthetic event ledger (4,096 events). Its `mojum.lifecycle.profile/1` and `mojum.lifecycle.snapshot/1` schemas are separate from the accepted `mojum.lifecycle.synthetic-populations/1`.
- It keeps elapsed virtual ticks independent of explicitly supplied development and learning units. No age-only senescence or compulsory food/water behavior exists.
- Region counts, synthetic synapses, and pruning are test fixtures, not anatomical measurements or actual acquired skill.
- SHA-256 revisions support local compare-and-swap checks and accidental-change detection. They are **not authentication**, persistent cross-process CAS, atomic cross-module restore, or proof of custody.
- No automatic conversion between the two lifecycle APIs is provided. Unsupported snapshots must fail closed.

## Acceptance

From repository root, Python 3.11+:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lifecycle_event_ledger*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lifecycle_abi_isolation.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lifecycle.py' -v
```

Before promoting this from opt-in prototype, require exact-head CI on all configured Python versions and independent code review. This is a conflict-free port of unique #173 functionality, not a claim that #173 can be merged unchanged.
