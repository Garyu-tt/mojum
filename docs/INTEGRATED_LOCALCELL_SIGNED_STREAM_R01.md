# R0.14 — merge-independent compatibility: v3 + streaming + HMAC

This **separate Draft integration** is based on the exact
[PR #101 HMAC-accelerated stream](https://github.com/Garyu-tt/mojum/pull/101)
head `b7a4cbb07d501d454213121dcb288bf731467f68` and
ports only explicit Local Cell v2/v3 policy versioning from the
independently tested [PR #122](https://github.com/Garyu-tt/mojum/pull/122).
It is deliberately not a merge/rebase of the two peer-owned histories.

## Confirmed semantic boundaries

- Streaming journal reader, prior SHA-chain verification, lazy
  `after_seq` tail read, 100,000 *total events* cap, retained CAS
  checks and HMAC cached-state verification remain from #101.
- The default `legacy` manifest stays exactly as in #101.
  `local_cell_v2` and `local_cell_v3` are explicit opt-in modes
  pinned in the **new PRIVATE journal bootstrap**. The policy schema
  is also recorded per `developmental` frame.
- The signed snapshot loader dispatches through the exact policy
  class pinned by the bootstrap rather than always reconstructing
  legacy `Explorer`. An invalid but newly signed v2 state presented
  to v3 fails before adopting RAM state; a valid HMAC alone cannot
  reinterpret a policy.
- For v3, the 512-cell LRU cap and forgotten-observation counters
  are exactly from the original reviewed policy module. This
  compatibility work changes neither v2 nor v3 motor semantics.
- The signed snapshot stays PRIVATE *plaintext*, with HMAC
  integrity but not encryption or automatic rollback prevention.
  Every reopen still scans the **entire** SQLite hash chain; signed
  replay skips only already-verified deterministic simulation.
- `Proprioception.MAX_EPISODE_TICKS=9999` still applies. The
  100k journal limit is **not** a claim that v3 can execute 100k
  goal-free ticks. The `portable_host` now preflights the entire bounded
  requested session against the remaining 9999-tick episode capacity
  BEFORE making the first SQLite frame append, rather than half-running
  a session and failing at the boundary. Future independently versioned
  proprioception and epoch rollover are needed, not a silent tick modulo.
- The existing CPython/Termux-compatible foreground host has an explicit
  `--developmental-policy local_cell_v3` option; default remains
  `legacy`. Its existing `--snapshot-file` and `--snapshot-key-file`
  opt-in continue to work with a v3-pinned private journal.

Portable opt-in example (NEW source journal only):

```bash
PYTHONPATH=src python -m mojum.portable_host \
  --db /private/path/new-v3.sqlite3 --ticks 120 --interval-ms 1000 \
  --exploration-mode goal_free --developmental-policy local_cell_v3 \
  --snapshot-file /private/path/new-v3.replay.json \
  --snapshot-key-file /private/path/separately-provisioned.key
```

The HMAC key must have been explicitly provisioned separately with
`python -m mojum.verified_snapshot --create-key ...`. The snapshot
is not encrypted. This is a foreground CPU-only run, **not** an Android
background service or permission to connect real actuators.

## Reproduction

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_localcell_signed_stream.py -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_cell_policy_v3.py -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_localcell_portable_host.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Independent test fixtures use disposable SQLite databases, explicit
0600 key provisioning, signed prefix at tick 80, 25 extra durable
frames, checks on tail-only deterministic replay, full hash-chain
validation and policy-specific v1/v2/v3 startup/replay. Bad
correctly HMAC-signed model snapshots are rejected.

No actual private user memory, physical motor, GPU, phone, neural
brain, MuJoCo body or direct production VM was touched. Keeping
separate review branches protects the peers' 100k streaming and
HMAC source work until integration is independently reviewed.
