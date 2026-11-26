# Opt-in cloth LOD admission planner (Issue #376)

Status: **isolated CPU-stdlib research slice**, NOT enabled in the synthetic
Aquarium, body adapter, MuJoCo, Jolt, or the PRIVATE journal. No runtime assets,
data, permissions, hardware actuation, network or new dependencies.

## Contract

`mojum.cloth_lod` plans *whether* a separately supplied cloth solver may run,
not how garments deform. `ClothLODConfig()` is **off by default**. The caller
provides its *own* (measured, mesh-specific) LOW/MEDIUM/HIGH cost estimates and
a machine-specific CPU microsecond budget. Hard resource admission checks
estimated vertices, constraints and memory; they are **not allocator caps** and
must not be advertised as a verified RSS limit.

| Mode | Base-tick cadence | Substeps when due |
|---|---:|---:|
| off | never | 0 |
| low | every 4 ticks | 2 |
| medium | every 2 ticks | 2 |
| high | each tick | 2 |

Cadence follows the **absolute tick number** so cold restarts do not change
the schedule. The host must implement elapsed-time integration, correct solver
collision/anchor handling, and explicit fixed-dt physics. No `dt`, solver
or real hardware driver is configured by this planner.

### Example, disposable synthetic observation only

```python
from mojum.cloth_lod import ClothLODConfig, start, advance

config = ClothLODConfig(requested="low", cpu_budget_us=8000)
state = start(config)
for tick in range(12):
    # Real callers MUST measure the preceding scheduled cloth step.
    previous_cpu_us = 1200 if state.pending_measurement else None
    decision, state = advance(config, state, tick, previous_cpu_us)
    if decision.simulate:
        print(tick, decision.effective, decision.substeps)
snapshot = state.to_snapshot()
```

A future integration may supply measurements from e.g. a local per-process CPU
counter; these tests use explicit **synthetic measurements**, not benchmarks.
`completed_cpu_us` always describes the **immediately preceding scheduled**
cloth step. It must be a bounded nonnegative integer; unsolicited data and
nonsequential ticks raise `ClothLODError` before changing state.

- Actual measured cost **greater than** the configured CPU budget downgrades
  HIGH -> MEDIUM -> LOW -> OFF. OFF after LOW overrun is latched until
  explicit operator restart. The caller must independently apply a whole-host
  safety guard; even one solver step can exceed budget.
- Four measured safe **steps** (not unmeasured or skipped ticks) allow one
  recovery step toward the resource-admitted requested profile.
- A scheduled step without an available completion measurement on the next
  tick latches OFF, with an observable `measurement_missing` reason.
- Fixed configuration and measurement sequence produce the same decisions.
  The state is a strict `mojum.cloth.lod-state/1` JSON-compatible snapshot
  with a configuration fingerprint, exact fields and monotonic tick.
  Restore rejects unknown schemas/configs rather than migrating implicitly.
- Fingerprints are unkeyed SHA-256 for accidental mismatch detection, **not
  authentication**, rollback prevention or encryption. Do not store snapshots
  in public logs if an integrating application adds PRIVATE metadata.

This planner intentionally never touches a source journal, body state,
organism identity, emergency stop or real contact sensors. Its output is only
advisory until a separate physics integration provides verified CPU-cost
measurements, solver tests, no-penetration/anchor checks and independent QA.

## Reproducible offline test

```sh
PYTHONPATH=src python -m unittest tests/test_cloth_lod.py -v
```

Coverage: no mandatory engine imports, default OFF, cadence/substeps,
three resource limits, overload downgrade, safe-sample recovery, missing
measurement fail closed, schema/fingerprint and typed input rejection, plus
10,050 synthetic decision ticks with equivalent cold state restore.

The tests do **not** demonstrate actual cloth motion, MuJoCo compatibility,
realtime deadlines, RSS behavior, Android reliability or consciousness.
