# Mikoshi coordination and autonomic contracts R01

R01 provides two opt-in, CPU-only fixed-point interfaces:

- `mojum.cerebellar_coordination` turns caller-supplied synthetic posture,
  velocity and support errors into a bounded correction advisory;
- `mojum.autonomic_homeostasis` turns caller-supplied synthetic energy, load
  and thermal-deviation signals into bounded arousal, rest-drive and internal
  rhythm advisories.

They are deliberately separate. A coordination fault does not mutate the
autonomic state, each interface has its own cadence and profile digest, and
callers may schedule or restore either one without the other. Both use logical
integer ticks only; neither reads wall-clock time, files, journals, devices or
the network.

## Safety and claim boundary

These modules are engineering fixtures. They are not a biological cerebellum,
autonomic nervous system, consciousness model, medical monitor, calibrated
posture controller, physics engine or motor authority. Inputs must be marked
`PUBLIC_SYNTHETIC`. Outputs are advisory integers and do not actuate anything.

In particular, a `correction_milli` result is not a joint target. A caller that
chooses to translate it into a motor intent must separately use the existing
revisioned `MotorGate` and its configured joint bounds. R01 does not bypass,
clear or replace an e-stop.

## Versioned profiles and opt-in

`default_profile()` returns `enabled=False`. Execution therefore requires an
explicit profile such as:

```python
from mojum import autonomic_homeostasis as autonomic
from mojum import cerebellar_coordination as coordination

c_profile = coordination.default_profile(enabled=True)
a_profile = autonomic.default_profile(enabled=True)
c_state = coordination.initial_state(revision=4)
a_state = autonomic.initial_state(revision=4)
```

The profile schemas are:

- `mojum.mikoshi.coordination-profile/1`;
- `mojum.mikoshi.autonomic-profile/1`.

Profiles are frozen exact dataclasses. Native booleans and integers are
required, unknown schemas are rejected, every bound has a hard ceiling, and a
profile with no active feedback signal is invalid. Each snapshot contains the
SHA-256 digest of the complete profile. Restoring under a different cadence,
gain, bound or schema fails closed.

## Coordination transition

`CoordinationObservation` supplies one revisioned sample with three signed
fixed-point errors:

- `posture_error_milli`;
- `velocity_error_milli`;
- `support_error_milli`.

At the configured global cadence, the controller applies integer gains,
negates the weighted error, clamps it to `max_correction_milli`, applies the
deadband and rate-limits change to `max_delta_milli`. Division is explicitly
toward zero, so behavior does not depend on floating-point or ambient decimal
settings. Between coordination ticks the previous advisory is held.

A fault or sample older than `stale_after_ticks` emits zero immediately,
including between cadence ticks. Future observations, regressing observation
ticks, stale revisions and skipped/repeated transition ticks are errors. The
controller consumes exactly `state.next_tick` and advances it by one.

## Autonomic transition and internal rhythm

`AutonomicObservation` supplies bounded synthetic `energy_milli`,
`load_milli` and signed `thermal_deviation_milli`. At its independent cadence,
the controller combines:

- a profile-pinned base arousal;
- energy feedback around `energy_target_milli`;
- load feedback above `load_target_milli`;
- the magnitude of the synthetic thermal deviation;
- a deterministic integer triangular rhythm.

The triangular rhythm has no physiological interpretation. It is a repeatable
periodic test signal derived only from `tick % rhythm_period_ticks`. Arousal
and rest drive remain in `[0, 1000]` and may change by at most
`max_delta_milli` per autonomic update. A fault or stale sample immediately
emits `(arousal=0, rest_drive=1000, rhythm=0)`. This is a conservative fixture
state, not a medical or physical safety guarantee.

## Snapshots, replay and live restore

Both modules expose `snapshot(profile, state)` and
`restore(profile, payload, minimum_next_tick=None)`.

- snapshots are detached, flat, JSON-compatible dictionaries;
- envelope and state fields must match exactly and use native string keys;
- the complete profile digest is mandatory;
- counters, signals, revisions and observation ticks are revalidated;
- `minimum_next_tick` optionally prevents rollback when restoring into a live
  scheduler;
- omitting the floor permits intentional cold replay from an older snapshot.

Pure transitions plus profile-pinned snapshots make continuation deterministic:
identical ordered observations after a snapshot produce identical immutable
states and results.

## Bounded execution

There is no catch-up loop or hidden work queue. One call validates one profile,
one state and one observation, performs constant bounded integer work, and
returns one new immutable state/result pair. Counters are limited to signed
63-bit values and fail before overflow. The focused regression suite includes
independent-cadence execution, stale/fault paths, hostile snapshot keys,
profile mismatch and deterministic 10,000-tick dual-controller stress.

R01 does not yet connect these advisories to the separate spinal runtime,
Aquarium journals, GENESIS chemistry, MuJoCo or hardware. Such integration
requires an explicit versioned adapter, its own persistence/admission review,
and independent evidence that existing MotorGate and e-stop semantics remain
authoritative.
