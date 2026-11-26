# Mikoshi coordination runtime bridge R01

## Status and scope

This is an opt-in, CPU/stdlib-only orchestration layer for the existing
`cerebellar_coordination` and `autonomic_homeostasis` advisory contracts.
It supplies a shared logical tick and a single snapshot envelope while keeping
the two component schedules, profiles, state and fault counters independent.

The bridge is an integration primitive, not a completed spinal runtime or a
biological model. It does not write GENESIS Chemistry/Homeostasis, Aquarium or
Noosphere journals, invoke MuJoCo, drive motors, set cardiac/respiratory rates,
access hardware, or perform network I/O. Its outputs are synthetic advisory
values and must not be treated as calibrated physiology or an actuation
authorization.

## API

Construct `MikoshiCoordinationRuntime` with explicit validated component
profiles and a shared nonnegative revision. Profiles default to disabled;
callers must explicitly opt in by passing enabled profiles.

For each logical tick, pass one
`CoordinationObservation` and one `AutonomicObservation` to `step(...,
tick=n)`. Both observations must use the runtime revision and be valid at
that tick. The coordination and autonomic contracts independently decide
whether their cadence is due. A component-reported fault resets only that
component's advisory output; the other component continues if its own sample
is valid.

Both transitions are calculated before either state is committed. If either
input is invalid, the call raises and neither component state nor the shared
clock advances. This is an in-memory atomicity guarantee only; it is not a
cross-process transaction.

`snapshot()` returns a detached envelope containing the component snapshots
and shared clock. `restore(..., minimum_next_tick=...)` checks exact schema,
profile digests, shared revision and clock agreement, and can reject rollback
below a caller-provided live tick. `snapshot_json()` emits deterministic
canonical JSON suitable for caller-managed persistence.

## Scheduling

The shared clock advances exactly once per successful call. Component
cadences remain local to their own profiles (for example, coordination every
2 ticks and autonomic updates every 5 ticks). The bridge does not create
threads, timers, wall-clock scheduling or asynchronous inference.

## Safety and limitations

- Inputs must be the component contracts' `PUBLIC_SYNTHETIC` observations.
- Native integer/bool, range, tick, revision, stale-sample and fault checks
  remain enforced by the underlying component APIs.
- Defaults are disabled and fail to neutral/hold outputs.
- No output is sent to a motor, body, cardiovascular/respiratory generator,
  journal or external service.
- A snapshot digest is a consistency checksum, not an authentication
  signature or independent trust anchor.
- Independent exact-head CI and QA are required before acceptance.
