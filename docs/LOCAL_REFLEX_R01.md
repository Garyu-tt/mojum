# Local reflex CPU fixture R0.1

This opt-in implementation advances the bounded controller portion of
[#1359](https://github.com/Garyu-tt/mojum/issues/1359). It consumes current virtual
joint observations, selects local responses without waiting for cognition, and
passes every returned position target through the existing `MotorGate`.

The module is standard-library-only and uses explicit `PUBLIC_SYNTHETIC`
fixtures. It does not actuate a body, estimate physical truth, claim biological
equivalence, or model subjective pain. Integration with the separately developed
spinal scheduler ([#1358](https://github.com/Garyu-tt/mojum/issues/1358)), a calibrated
body and actual MuJoCo execution remains open. This contribution does not close
the parent integration task or its epic.

## Run and inspect

From the repository root:

```sh
PYTHONPATH=src python examples/local_reflex_demo.py
PYTHONPATH=src python examples/local_reflex_demo.py --ticks 1000 --joints 64
PYTHONPATH=src python -m unittest discover -s tests -p 'test_local_reflex.py' -v
```

The demo verifies withdrawal, stretch, crossed support, force inhibition,
velocity-perturbation response and reciprocal inhibition. It also exercises a
stalled cognitive source, exclusive intention expiry, deterministic checkpoint
continuation and the gate's latched emergency stop. Failed behavior checks exit
with an error. Observations are scripted inputs; the demo does not move a body or
replace measured feedback with command targets.

JSON includes the actual output frames and per-step `perf_counter_ns` latency
percentiles (nearest-rank p50, p95, p99 and maximum), plus total process CPU and wall
time. Step timing excludes fixture construction; total timing includes per-tick
fixture construction and invariant checks. Results depend on the host and load.
No timing value is an enforced real-time guarantee or a biological frequency.
`--ticks` is bounded to 1–10,000; `--joints` accepts even counts from 2 to 64.

## Public API and settings

```python
from mojum.local_reflex import JointReflex, LocalReflexController
from mojum.motor_gate import MotorGate

gate = MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, revision=7)
profiles = (
    JointReflex("left", partner="right", withdrawal_direction=1),
    JointReflex("right", partner="left", withdrawal_direction=-1),
)
controller = LocalReflexController(gate, body_id="fixture-body", joints=profiles)
# frame = controller.step(observation, intent, evaluated_at_ns=simulation_time_ns)
```

`JointReflex` settings are detached and validated at controller construction.
There must be 1–64 distinct controlled joints present in the supplied gate.
Optional contralateral partners must be distinct and symmetric. Neutral positions
must lie within the gate's joint limits. Directions are exactly `-1` or `1`;
positive position direction is the fixture's flexion convention.

| Setting | Default | Meaning |
| --- | --- | --- |
| `neutral_rad` | 0 | Recovery/support target |
| `max_step_rad` | 0.05 | Maximum target displacement from the current measured angle per accepted observation |
| `withdrawal_step_rad` | 0.05 | Signed withdrawal displacement before bounds |
| `stretch_band_rad` | 0.4 | Neutral displacement above which recovery takes priority |
| `velocity_threshold_rad_s` | 1 | Absolute measured speed above which damping takes priority |
| `damping_s` | 0.05 | Velocity-to-position-offset coefficient |
| `torque_limit_nm` | 10 | Absolute measured torque proxy that inhibits target displacement |
| `load_limit_n` | 100 | Measured nonnegative load proxy that inhibits target displacement |

These are engineering test values. A per-observation displacement bound is not a
physical velocity/acceleration limit: observation spacing is not fixed by this
module. The caller supplies deterministic simulation time. Neither wall-clock
sleep nor an inference worker is part of the controller.

## Versioned observations

Each call accepts an exact native dictionary:

```json
{
  "schema": "mojum.local-reflex.observation/1",
  "body_id": "fixture-body",
  "revision": 7,
  "physics": {
    "schema": "mojum.virtual-physics-observation/1",
    "tick": 0,
    "time_ns": 0,
    "backend": "fixture",
    "synthetic": true,
    "physical_contact": false,
    "physical_actuation": false,
    "joints": [
      {"id": "left", "position_rad": 0.0, "velocity_rad_s": 0.0},
      {"id": "right", "position_rad": 0.2, "velocity_rad_s": 0.0}
    ],
    "virtual_contacts": []
  },
  "proprioception": [
    {"joint": "left", "torque_nm": 0.0, "load_n": 0.0, "contact": false, "support": false, "hazard": true},
    {"joint": "right", "torque_nm": 0.0, "load_n": 0.0, "contact": true, "support": true, "hazard": false}
  ]
}
```

Positions and velocities pass the existing virtual-physics validator. This
extension binds them to the controller's body and gate revision; `proprioception`
describes the same sample as the nested packet. Both lists must cover exactly the
controlled joint set. There are at most 256 virtual contacts. Contact/support/hazard
are exact booleans, and support requires contact. Torque is signed; load is
nonnegative. NaN, infinities, booleans masquerading as numbers, duplicate/missing
joints, foreign bodies, unsupported schemas and out-of-range angles are rejected.
The packet is a caller assertion, not an authenticated sensor measurement.

Ticks start at zero and must be contiguous. Sample timestamps strictly increase;
evaluation time cannot go backwards. A sample is admitted when
`sample_time <= evaluated_at <= sample_time + max_observation_age_ns`. The default
maximum age is 50 ms, inclusive at the boundary. Future or stale samples reject
the whole step. No rejected step advances the controller clock or cached intent;
the caller must treat an exception as no new usable command frame.

## Cognitive intentions and local priority

An optional `mojum.local-reflex.intent/1` dictionary contains exactly `schema`,
`body_id`, `revision`, `sequence`, `issued_at_ns`, `expires_at_ns` and `targets`.
`targets` maps a nonempty subset of controlled joint names to finite in-limit
position targets. The lifetime must be positive and no greater than the configured
maximum, 500 ms by default. Identifiers, integer counters and fields use strict
native types and bounded shapes.

Omitting an intention retains the last accepted one. It expires at
`evaluated_at_ns >= expires_at_ns`; no new cognitive packet is needed to expire it.
The sequence high-water mark survives expiry. Well-formed stale-revision or
replayed packets are ignored, leaving any still-valid current intention in place.
A newer, already-expired packet is also ignored and advances the sequence
high-water mark; an older cached intention can remain active until its own expiry.
Their status is reported, while local reflexes keep running. Future or malformed
intentions reject the entire call. In particular,
an out-of-limit external target is rejected rather than silently repaired.

For each joint the first matching response wins:

| Priority | Condition | Position proposal |
| --- | --- | --- |
| Emergency stop | Existing gate latch is set | Empty command list for the entire frame |
| Force limit | Absolute torque or load is at/above its threshold | Hold measured angle; zero normalized activation |
| Withdrawal | Current joint hazard | Move in configured withdrawal direction |
| Crossed support | Paired joint hazard and current joint contact/support | Move toward neutral |
| Stretch | Absolute neutral displacement exceeds its band | Move toward neutral |
| Perturbation | Absolute velocity exceeds its threshold | Oppose velocity with a bounded damping offset |
| Cognitive | Fresh cached target exists for this joint | Approach that target |
| Hold | None of the above | Hold measured angle |

Proposals are limited relative to the current measured angle, then saturated at
configured joint limits. The actual supplied `MotorGate.validate(MotorIntent(...))`
remains the final validator for every returned target. Gate rejection causes no
partial frame or controller-state commit. If the gate revision changes, a compatible
new controller must be constructed. The controller is single-threaded.

Each command reports its source and `flexor`/`extensor` values in `[0, 1]` derived
from the bounded target displacement. At most one is nonzero: this is the fixture's
reciprocal-inhibition rule. They are normalized explanatory proxies, not muscle
activation or torque commands. Holding the measured angle and zeroing these
proxies does **not** guarantee reduced physical force. That requires an independently
accepted actuator/physics controller and force-feedback policy.

## Checkpoint and continuation

`snapshot()` returns detached `mojum.local-reflex.snapshot/1` data containing the
profile fingerprint, body/revision, next tick, sample/evaluation clocks, cached
intention, sequence high-water mark and emergency-stop latch. Its `sha256` checks
canonical consistency.

```python
checkpoint = controller.snapshot()
pinned_digest = checkpoint["sha256"]  # Pin in the caller's trusted checkpoint record.
restored = LocalReflexController.from_snapshot(
    gate, body_id="fixture-body", joints=profiles,
    snapshot=checkpoint, expected_sha256=pinned_digest,
)
```

Restoration requires matching settings and gate revision plus an independently
supplied expected digest. It preserves an already-latched live gate and propagates
a saved latch to a new gate. A snapshot cannot clear emergency stop. Matching input
continuations produce matching frames and checkpoints; latency fields are outside
controller state.

The digest is not authentication. This API does not write a file, implement a
durable journal, prevent caller-selected rollback, or prove physical continuity.
The caller owns snapshot provenance, durable atomic storage and freshness.

## Acceptance boundary

The focused workflow runs the new behavior tests together with existing MotorGate
and virtual-physics tests on Python 3.11, 3.12 and 3.13. It checks out and asserts the
actual PR head and executes the 64-joint fixture. Timing is reported without a
host-dependent pass threshold. Full repository checks and independent review remain
required before merge. Runtime routing, calibrated sensing, articulated recovery,
physical force guarantees and locomotion remain separate integration work.
