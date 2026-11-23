# R0.1 disposable Aquarium cold-restart smoke (issue #922)

The standalone `tools/offline_restart_smoke.py` is a single-host QA helper for
parent #11. Run from the checkout with Python 3.11+:

```sh
python tools/offline_restart_smoke.py
python -m unittest discover -s tests -p 'test_offline_restart_smoke.py' -v
```

No third-party dependency, network service, host-specific path, or daemon is
required. The script itself does not call the network, but it is **not** an OS
network sandbox or a proof that all imported code has no network capability.
It constructs an owned disposable `TemporaryDirectory`, runs the real
`mojum.aquarium` CLI as three child processes against that journal, and cleans
it up. It does **not** accept a journal path from the operator.

- First process: create a synthetic, `neural_mode=disabled` journal and append
  three frames (configurable `--first-ticks`, 1..32).
- Second: cold restart with a pinned prior journal head and append two frames
  (`--second-ticks`, 1..32).
- Third: cold restart with `--ticks 0` and the latest expected head; assert
  bootstrap was not repeated and the journal head did not change.
- Validate exact checkpoint tick and `1+tick` event counts each time; durable
  identity, manifest SHA-256 and genome revision must remain unchanged. Reject
  regressing time/world revision and verify `PRAGMA integrity_check` on a
  separate read-only SQLite connection with deterministic closure.
- Report a single JSON status with **metadata only**, never record payloads,
  full checkpoint, agent ID, journal head, DB path, child stdout or stderr.
  Failures use deliberately generic reasons to avoid disclosing child output.

The process timeout is adjustable with `--timeout` (5..120 seconds per run).
Bounds are intended for normal synthetic fixtures, not defense against a
malicious Python interpreter or a fork bomb. There is no explicit virtual
wall-clock pacing; each process runs only a tiny batch.

**What PASS means:** the CLI booted, committed synthetic frames, replayed a
second process boundary, preserved the expected durable head during a zero-tick
reopen and passed one local SQLite integrity check. This does not prove
snapshot encryption, PRIVATE dataset export safety, no leaked PRIVATE data in
other components, simultaneous writer fencing, distributed durability, MuJoCo,
a genuine neural system, mobile portability, real actuator safety or 24/7 life.
Existing Noosphere checksum trust is not authentication. Manual independent
QA and exact-head Python 3.11/3.12/3.13 workflows are required before merge.
