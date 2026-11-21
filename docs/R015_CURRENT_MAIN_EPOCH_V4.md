# R0.15 current-main epoch v4 slice

This slice adds an opt-in `motor_mode="local_cell_v4"` to the current
`Aquarium` runtime. It is a new persisted wire contract; legacy scripted,
v2A, v2B and v3 journals are not rewritten or reinterpreted.

The v4 path uses:

- `mojum.developmental.proprioception/2` with an absolute logical tick,
  explicit `epoch_index`, and `epoch_tick`;
- `mojum.developmental.local-cell/4` with the same bounded 512-cell retention
  accounting as v3, now valid across epoch boundaries;
- a separate `mojum.aquarium.epoch-transition/1` PRIVATE event at absolute
  ticks `9999`, `19998`, and so on, committed before the first frame of the
  next epoch;
- the existing bounded streaming replay path, with the v4 journal allowed to
  use the 100,000-event ceiling while old motor wires retain their 10,000-event
  ceiling.

If a process stops after the boundary marker but before the next frame, cold
replay adopts the marker and continues from the same absolute tick and learned
state. The marker is not a reset, a new organism, a reward, or evidence of a
neural brain. This remains a CPU-only synthetic point-room fixture with no
physical actuation, Android daemon, MuJoCo body, sensory-v2 integration, HMAC
snapshot sidecar, or claim of consciousness.

The acceptance test in `tests/test_aquarium_epoch_v4.py` creates a disposable
SQLite journal, reaches `9999` frames, injects the marker/frame interruption,
reopens cold, continues to `10050`, checks the wire inspector, and verifies
that opening the journal as v3 fails without changing SQLite bytes.
