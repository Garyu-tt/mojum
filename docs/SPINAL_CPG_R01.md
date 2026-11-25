# Spinal CPG R01: bounded offline controller contract

Status: deterministic CPU-only foundation for synthetic experiments.

This revision defines a small bilateral oscillator and its trust boundaries. It
emits dimensionless waveform samples. It does not emit joint targets, authorize
motors, call MotorGate, integrate a simulator, or communicate with hardware.
The waveform is an engineering test signal, not a claim of biological fidelity.

## Units and admitted ranges

All state transitions use Python standard-library integer arithmetic. No clock,
random source, thread, floating-point operation, NumPy, or platform-dependent
math function participates in a transition.

| Field | Unit | Admitted range |
| --- | --- | --- |
| phase | millionth of one cycle | `0..999999` |
| tick period | microseconds | `1000..100000` |
| frequency | millihertz | `10..5000` |
| amplitude | thousandth of full scale | `1..1000` |
| load feedback | thousandth | `0..1000` per side |
| frequency feedback | millihertz delta | `-5000..5000` |
| tick | monotonic integer | `0..1000000000` |

`CPGProfile` is frozen and revisioned. Admission rejects booleans, floats,
out-of-range integers, unordered frequency limits, and a sensory gain above the
configured correction limit. A profile's SHA-256 covers every profile field.
The maximum frequency/tick/correction combination is also rejected if either
side could advance more than half a cycle in one transition. This admission
uses the ceiling of the maximum frequency advance so a carried fixed-point
remainder cannot add an unaccounted microturn on a later tick.

`SensoryFeedback` is frozen, explicitly synthetic, bound to one profile
revision, and accepted only for the next contiguous tick. Missing, repeated,
future, or wrong-revision feedback fails closed without advancing state.

## Transition

For each admitted record, the controller performs this deterministic sequence:

1. Add the requested frequency delta to the base frequency and clamp it to the
   profile's minimum and maximum.
2. Convert frequency and tick duration to an integer phase advance with
   `divmod(frequency_millihz * tick_us + remainder, 1000)`. Preserve the
   remainder in state, so splitting a run across a snapshot does not lose time.
3. Measure the shortest signed deviation from a half-cycle bilateral offset.
4. Combine bounded proportional coupling and the bounded right-minus-left load
   term. Clamp their total both to the profile correction limit and to half of
   the current forward phase advance. Thus feedback cannot reverse either side.
5. Advance the left side by `advance + correction` and the right side by
   `advance - correction`, modulo one cycle.
6. Map each phase through an exact fixed-point triangle waveform and scale it by
   the admitted amplitude. Every output remains in `-amplitude..amplitude`.

Frames include phases, the admitted frequency, pre-correction coupling error,
applied correction, profile identity, and resulting state identity. They state
`physical_actuation=False`; consumers must not interpret them as motor commands.

## Snapshot and replay contract

`snapshot()` returns a frozen `CPGSnapshot` containing the schema, profile
revision and digest, tick, both phases, and the frequency remainder. Its state
SHA-256 covers those fields using canonical JSON plus a schema domain separator.

`BilateralCPG.from_snapshot(profile, snapshot)` validates exact input types,
schema, every numeric bound, lowercase SHA-256 syntax, the full profile binding,
and the state digest before restoring anything. A changed profile or snapshot
fails closed. Given the same admitted feedback sequence, an uninterrupted run
and a restored run produce equal frames and final snapshots.

## Non-goals and acceptance boundary

R01 deliberately makes no claim about:

- biological or neurological accuracy;
- standing, balance recovery, gait quality, or locomotion;
- MuJoCo or any other physics environment;
- robot joints, torque, current, voltage, MotorGate, USB, or hardware;
- real sensor calibration, timing guarantees, or safety certification.

Those require separate integrations and measured acceptance criteria. R01 is
accepted only as a bounded, deterministic, replayable signal generator whose
adversarial admission, long-run bounds, perturbation recovery, snapshot
tampering, and replay behavior pass `tests/test_spinal_cpg.py`.
