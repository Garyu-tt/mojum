# Explicit synthetic controller boundary R0.1

The canonical Aquarium already has an opt-in `developmental_fixture` motor
policy. This slice gives that policy an explicit, versioned brain boundary in
new journals instead of leaving the committed developmental path hidden next
to `brain.placeholder`.

Run it locally with one CPU and no network service:

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./controller.sqlite3 \
  --ticks 24 \
  --neural-mode shadow \
  --brain-mode developmental_controller \
  --motor-mode developmental_fixture \
  --sensory-mode toy_probe \
  --temporal-mode range_guided
```

Each committed frame contains a
`mojum.neural.developmental-controller/1` record with:

- the current synthetic heartbeat and numeric proprioception;
- the optional numeric near-field cue and numeric range guidance;
- an action selected by the existing replayable `MotorPlay` state;
- a bounded point-room safety declaration with external override and real
  actuation disabled.

The speculative action, world transition, learner and motor snapshot are
still appended through the Aquarium's one SQLite CAS event before RAM state is
adopted. Reopening the same journal requires the same explicit mode and
recomputes the exact controller record. The old default mode and all existing
journals retain their `brain.placeholder` / `UNIMPLEMENTED` contract.

This is a useful synthetic sensorimotor controller boundary, not a trained
neural network, general intelligence, consciousness, camera perception,
MuJoCo body, physical motor authority, authentication or distributed
failover. The near-field model receives numeric cues only; spectator toy IDs,
softness, obstacle geometry and future observations remain outside its input.


