# R0.15 explicit epoch continuation on current main

`local_cell_v4` is an opt-in, CPU-only continuation profile for the current
Aquarium API. It addresses the old motor policy's 9,999-tick typed
proprioception ceiling without changing any existing v1, v2, or v3 journal.

It introduces three distinct wire contracts:

- `mojum.developmental.proprioception/2` carries a monotonic absolute tick,
  an epoch index, and an epoch-local tick. At absolute tick `9999` the value
  is epoch `1`, local tick `0`; it is never silently reduced modulo 9,999.
- `mojum.developmental.local-cell/4` retains the bounded deterministic v3
  local-cell policy while pinning its snapshots and outcomes to the new
  observation type.
- `mojum.aquarium.epoch/1` is a durable PRIVATE marker appended before the
  first frame of each later epoch. Replay rejects a missing, duplicated,
  forged, or out-of-order marker before adopting the following frame.

Start a new profile explicitly:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./epoch_v4_PRIVATE.sqlite3 \
  --ticks 1000 \
  --motor-mode local_cell_v4 \
  --neural-mode disabled
```

Repeat the command with the same flags to continue. The profile currently
accepts only `--sensory-mode none`; the bounded `toy_probe_v2` contract is not
silently mixed into this epoch wire. `aquarium_wire` reports `local_cell_v4`
and counts epoch markers separately from frames.

The implementation remains a synthetic point-room controller. The journal is
still bounded at 100,000 total events. Because bootstrap and durable epoch
markers consume that same budget, this Aquarium profile admits at most 99,990
v4 frames (`1 bootstrap + 99,990 frames + 9 markers`). The underlying typed
proprioception schema remains a separate finite contract; it is not a promise
that one Aquarium journal can exceed its event ceiling. The SQLite head is a
checksum chain rather than authentication, and there is no neural brain,
camera, MuJoCo body, physical actuation, Android daemon, distributed replica,
or evidence of consciousness.
