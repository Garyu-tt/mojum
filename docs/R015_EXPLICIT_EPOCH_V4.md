# R0.15 explicit epoch-v4 continuation

`local_cell_v4` is an opt-in, CPU-only continuation policy for a **new**
PRIVATE Aquarium journal. It addresses one concrete capacity boundary in the
existing synthetic motor fixture: v1/v2/v3 proprioception intentionally stops
at 9,999 ticks.

## Contract

- New journals use `mojum.developmental.local-cell/4` and
  `mojum.developmental.proprioception/2`.
- The logical clock is `absolute_tick`; `epoch_index` and `epoch_tick` are
  derived from the fixed 9,999-tick epoch size. There is no modulo reset of
  the journal, world clock or event identifiers.
- At each boundary, an explicit `mojum.aquarium.epoch-transition/1` event is
  committed before the first frame of the next epoch. If a process stops
  after that commit, cold replay resumes at the marker with no invented frame.
- The v4 policy retains at most 512 local cells with deterministic LRU
  eviction. Evictions are accounted for as forgotten evidence, not treated as
  reward, pain or a learning signal.
- `sensory_mode=toy_probe` is refused for v4. A separate versioned sensory
  retention contract is required before crossing that model's own limits.
- Existing legacy, v2A, v2B and v3 journal bytes and replay rules are
  unchanged. Opening a v4 journal with another motor mode fails closed.

The absolute tick envelope is finite (`0 <= absolute_tick < 99,999`) and the
SQLite event budget is finite. This is a bounded continuation experiment, not
an infinite-run guarantee, distributed replica, physical device service,
neural brain or claim about subjective experience. `checkpoint/2` remains a
local checksum-only report, not an authenticated snapshot.

## Reproduce

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./epoch_v4_PRIVATE.sqlite3 \
  --ticks 1000 --motor-mode local_cell_v4 --sensory-mode none --neural-mode disabled
PYTHONPATH=src python -m mojum.portable_host --db ./epoch_v4_PRIVATE.sqlite3 \
  --ticks 100 --interval-ms 20 --motor-mode local_cell_v4 \
  --sensory-mode none --temporal-mode none --neural-mode disabled
PYTHONPATH=src python -m mojum.aquarium_wire --db ./epoch_v4_PRIVATE.sqlite3
```

The focused QA creates and cold-replays a real 10,050-frame disposable
SQLite history, injects a stop between the durable boundary marker and its
first frame, checks the marker, and verifies the final chain after restart:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_epoch_v4.py' -v
```
