# CPU-first synthetic virtual daylight fixture (R0.1)

**Parent:** [world simulator #7](https://github.com/Garyu-tt/mojum/issues/7). This is an **opt-in detached virtual observation**, not a change to the canonical Aquarium, physics, neural substrate, or journaling runtime. No claim of sleep, perception, real illumination, geography, external-world synchronization, or organism consciousness.

`mojum.world_daylight.DaylightCycle` defines exactly four positive integer phase durations in logical ticks (`dawn`, `day`, `dusk`, `night`), bounded to a total of 1,000,000, with an optional cycle-local offset. Projections use only an explicit logical tick: never wall time, locale, network, sensors, private Noosphere journals or external clocks. `light_permille` is integer synthetic lighting metadata (0..1000), not measured lux.

## Reproducible example

```python
from mojum.world_daylight import DaylightCycle, initial_state, advance, snapshot, restore
cycle = DaylightCycle(dawn_ticks=2, day_ticks=3, dusk_ticks=2, night_ticks=3)
state = initial_state(cycle)
state, observed = advance(cycle, state, tick=1, expected_revision=state.revision)
cycle2, state2 = restore(snapshot(cycle, state))
assert (cycle2, state2) == (cycle, state)
assert observed["phase"] == "dawn"
```

`advance` follows the same one-tick + revision-CAS pattern as `world_room.advance`. Fields: `schema=mojum.world.daylight/1`, `tick`, `cycle_revision`, `state_revision`, `cycle_index`, `phase`, `phase_tick`, `phase_duration_ticks`, `light_permille`, `phase_changed` (advance only), `provenance`. Call both world/daylight transitions with the same explicit logical tick if opt-in integrated later. Any canonical journal integration must introduce a **separate versioned bootstrap**, not silently change existing on-disk schemas.

Snapshot format: canonical ASCII JSON, max 4 KiB, exact keys and native integer types, no duplicate keys or non-finite values. Restore validates state/config digests, all phase and tick bounds, and canonical bytes. Logical tick is capped at `2**53-1` for common JSON clients. SHA-256 here detects **internal inconsistency only**; it is not authentication, provenance or anti-rollback. An authoritative journal requires a separately trusted authenticated root.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_daylight.py' -v
```

**Isolation:** only this new module, its tests, and this document. No modification to `main`, `world_room.py`, Aquarium, authority journal, device integration, other agents' branches or shared schedulers. Independent QA/SHIP and exact-head checks are required before merge.
