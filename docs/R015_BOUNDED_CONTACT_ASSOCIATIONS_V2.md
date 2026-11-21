# R0.15 — versioned bounded sensory associations and long-run v4 Aquarium

**Draft on [PR #132](https://github.com/Garyu-tt/mojum/pull/132)**.
The older `mojum.sensory-curiosity.association/1` and
`mojum.aquarium.toy-contact/1` behavior, old SQLite records,
HMAC replay-state/1 and other legacy controller policies are unchanged.

## Exact opt-in contract

A *new*, empty PRIVATE journal may explicitly select both:

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db /private/path/NEW-v4-with-contact.sqlite3 \
  --exploration-mode goal_free --developmental-policy local_cell_v4 \
  --sensory-mode toy_probe_v2 --ticks 120
```

Default v4 with `sensory_mode=none` still works unchanged. Old v4
`toy_probe` is explicitly rejected: no attempt to reinterpret old
association history. Older v1/v2/v3 motor policies cannot select the
new versioned contact bridge. A fresh bootstrap pins the following:

- `sensory_mode=toy_probe_v2`
- `sensory_model_schema=mojum.sensory-curiosity.association/2`
- `contact_bridge_schema=mojum.aquarium.toy-contact/2`
- `sensor_reprobe_period_ticks=2`

Unchanged old numeric near-field cue schema `mojum.sensor.synthetic-near-field/1`
and its occlusion contract remain, but the new *measured-learning* result
is versioned `mojum.sensory-curiosity.learning/2`. The new 2-tick
reprobe schedule is a disclosed behavioral change, NOT an automatic
upgrade of any previously committed experience.

## Bounded learned-state retention

- No more than **128 live numeric RGB cue associations**. To admit a new
  measured cue when full, evict the least-recently **measured** cue.
  String keys break ties deterministically. Repeated no-contact
  observations do not count as learned contact.
- The snapshot records live cue evidence counts/means,
  `last_seen_at`, monotonic `last_contact_tick`, `evictions`, and
  `forgotten_observations`. Strict accounting enforces
  `sum(live counts) + forgotten == updates`.
- This new research model supports **100,000 total measured contacts
  maximum** per journal, while the **100,000 total source event** cap
  usually stops the Aquarium earlier. There is no unlimited-memory
  or perpetual-organism claim.
- Model input is a low-level numeric hue/reflectance and range bucket
  plus an *actual measured* synthetic probe response; hidden toy
  ID/softness stay experimenter-only. A failed or forged frame never
  becomes adopted state without a canonical PRIVATE SQLite append.

## Test protocol and bias statement

In a deliberately small 0.32m synthetic room with one visible toy
whose contact radius covers the full point-room, the versioned bridge
has a 2-tick re-probe cadence. This is a **purposefully contact-rich
development fixture**, selected to stress the historic 4,000-contact
ceiling. It is not a random, blinded or independent natural-world
assessment.

The test creates 10,050 actual synthetic point-room Aquarium ticks,
including the 9999→10000 explicit epoch event, commits the full
PRIVATE SQLite history, saves a signed HMAC snapshot **after 8050**
ticks (>4000 contact measurements), then continues for 2000
additional ticks before comparing signed fast replay with full
unsigned replay. Separate synthetic direct-protocol tests inject
130 distinct colors to verify exactly bounded 128-cue LRU retention;
those direct cues are *not* a coherent physical scene.

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_sensory_v2_epoch.py -v
```

Portable CPU foreground-host reproduction (a *different* new journal):

```bash
PYTHONPATH=src python -m mojum.portable_host \
  --db /private/path/NEW-portable-synthetic.sqlite3 \
  --ticks 120 --interval-ms 1000 \
  --developmental-policy local_cell_v4 --sensory-mode toy_probe_v2
```

The integration QA also executes **two separate portable-host
subprocesses** with the same new PRIVATE SQLite journal and owner-only
HMAC replay cache, checking that the second advances the original
organism instead of creating a replacement. This is only a
CPython/Termux-compatible *foreground* test, not Android power-state
or daemon validation. The host publishes metadata only, never contact
or neural details to stdout.

## Open limits

- Only synthetic software: no real camera, tactile surface, MuJoCo,
  motor, Android background host or implemented full brain.
- This resolves the independent ~4000 contact association ceiling
  **within existing bounded journal limits**, but does not change
  100k total source events, v4's 99,999 absolute tick bound or
  HMAC plaintext/rollback risks.
- Strict independent schema/version review, genuine held-out
  perception tests, replay stress and eventually authenticated
  journal segment migration are still needed before any release.
  No source journal is compacted, erased or automatically migrated.
