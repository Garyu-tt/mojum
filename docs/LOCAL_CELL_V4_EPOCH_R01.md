# R0.15 bounded epoch continuation

`local_cell_v4` is an explicit, opt-in motor policy for the synthetic CPU
Aquarium. It introduces a new `mojum.developmental.proprioception/2` input
with an absolute monotonic tick plus `epoch_index` and `epoch_tick`; it never
reinterprets `Proprioception/1` or the persisted v2/v3 policies.

At absolute tick `9999`, the journal commits a separate
`mojum.aquarium.epoch-transition/1` marker before the first frame of epoch 1.
The marker is a normal SQLite CAS event. A restart after that marker and before
the next frame replays the same half-step, without resetting the motor model or
the world clock. The v4 policy keeps the v3-style bounded 512-cell retention
contract and records eviction accounting under a new `/4` schema.

Run it only with the built-in point room and `sensory_mode=none`:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./local_cell_v4_PRIVATE.sqlite3 \
  --ticks 1000 --motor-mode local_cell_v4 --neural-mode disabled
```

The existing bounded foreground host can pace the same profile on one CPU-only
device. Keep the v4 sensory and temporal selectors explicit:

```sh
PYTHONPATH=src python -m mojum.portable_host \
  --db ./local_cell_v4_PRIVATE.sqlite3 --ticks 1000 --interval-ms 1000 \
  --motor-mode local_cell_v4 --sensory-mode none --temporal-mode none \
  --neural-mode disabled
```

Its stdout is metadata-only; it preflights frame plus epoch-marker admissions
and leaves the PRIVATE SQLite journal as the only state owner.

Repeat the bounded command to continue the same private journal. The current
contract allows at most 99,990 frames so the nine epoch markers and bootstrap
remain within the 100,000-event SQLite budget. `aquarium_wire` identifies the
new ABI and checks marker/frame alignment without exporting private payloads.

This is a bounded replay/runtime milestone, not an always-on organism. The
current implementation uses the existing checksum-only local journal and does
not add the independently reviewed HMAC snapshot/segment continuation required
for a release beyond the event budget. It is not evidence of a trained neural
brain, consciousness, Android background reliability, MuJoCo physics, or real
actuation.
