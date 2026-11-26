# RoboPianist-style deterministic benchmark fixture R0.1

## Status and boundary

This is a **synthetic offline benchmark contract**, not RoboPianist, MuJoCo,
a hand model, a trainer, or an actuator integration. It adds no dependencies
to Mojum's CPU-first Python 3.11+ core. It performs no file/network access,
subprocess execution, audio playback, hardware access, or canonical state writes.

All generated examples are marked `PUBLIC_SYNTHETIC`. The scorer requires
explicit caller consent. Digests provide deterministic integrity checks over
the supplied values; they do not authenticate an author or prove consent.

## API

- `make_task(seed, held_out=False, note_count=12)`: create a deterministic
  bounded exercise over a synthetic one-octave key range.
- `validate_task(task)`: fail-closed validation and detached immutable note
  representation.
- `make_trace(task, mode="policy"|"frozen")`: produce a perfect reference
  trace or a deliberately simple frozen-key control.
- `score_trace(task, trace, consent=True)`: report pitch accuracy, duration accuracy and bounded
  mean absolute timing error.
- `compare_controls(task)`: compare the two fixtures without claiming
  learning, transfer, physical fidelity, or generalization.

The held-out split changes the generated task and is reproducible for a fixed
seed. It is only a fixture split, not a statistical evaluation protocol.

## Limits

The finger mapping is a fixed synthetic mapping. Timing is expressed in virtual
ticks. The control is intentionally simplistic. No physical forces, joint
kinematics, collision constraints, audio, latency, or real hand performance
are simulated or measured. A real RoboPianist/MuJoCo adapter must be a separate
optional package boundary with its own version/license review, resource limits,
and independent exact-head QA.
