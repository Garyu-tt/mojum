# R0.15 — versioned bounded synthetic contact associations

The canonical `toy_probe` mode keeps its original
`mojum.sensory-curiosity.association/1` contract and its fail-closed 4,000
measured-contact budget. This change adds a separate, opt-in `toy_probe_v2`
mode; it never reinterprets an existing SQLite journal.

## What the new mode provides

- `mojum.sensory-curiosity.association/2` is pinned in a new journal's
  bootstrap together with `mojum.aquarium.toy-contact/2`.
- At most 128 numeric RGB cue records remain live. A new cue at capacity
  evicts the least-recently measured cue, with the cue string as a
  deterministic tie-breaker.
- `updates`, `last_seen_at`, `last_contact_tick`, `evictions`, and
  `forgotten_observations` are persisted. Strict validation requires retained
  observations plus forgotten observations to equal total measured updates.
- A two-tick synthetic re-probe cadence permits a small contact-rich fixture
  to pass 4,000 measured contacts while staying within the current 10,000
  Aquarium-event replay budget. The runtime remains bounded and finite; it is
  not an always-on organism.
- The learner still receives only the numeric near-field cue and measured
  probe response. Toy IDs, softness and full scene geometry remain
  spectator-only. `external_actuator` stays `None`.

## Reproduce the opt-in path

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./sensory_v2_PRIVATE.sqlite3 \
  --ticks 120 \
  --motor-mode developmental_fixture \
  --sensory-mode toy_probe_v2

PYTHONPATH=src python -m mojum.aquarium_wire \
  --db ./sensory_v2_PRIVATE.sqlite3

PYTHONPATH=src python -m unittest -v \
  tests.test_sensory_associations_v2
```

Для ручной проверки перехода через старый потолок без включения долгого
сценария в обычный набор:

```bash
PYTHONPATH=src python tests/stress_sensory_associations_v2.py
```

Сценарий делает 8 001 шаг на маленькой локальной сцене, проверяет 4 001
измеренное обновление, число событий и cold replay из того же SQLite-журнала.

The journal is local, PRIVATE, plaintext SQLite with checksum-based replay
integrity only. This mode does not add a camera, physical touch, MuJoCo, real
actuation, authentication, encryption, distributed failover, object
semantics, a neural brain, or a consciousness claim.

## Compatibility boundary

Opening an existing `toy_probe` journal as `toy_probe_v2` (or the reverse)
fails before a new frame is appended. The original association schema and its
historical events are not migrated, compacted, reset, or silently rewritten.
The current Aquarium replay budget remains 10,000 events; epoch continuation
and physical segmented archives are separate release gates.
