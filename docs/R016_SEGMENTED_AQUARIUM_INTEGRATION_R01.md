# R0.16 — explicit segmented Noosphere in CPU-first Aquarium (Draft)

[Draft PR #139](https://github.com/Garyu-tt/mojum/pull/139)
adds an independent atomic logically segmented SQLite journal.
This *separate stacked integration* selects that backend in an actual
synthetic v4 Aquarium, without silently changing any old source data.

The default API and CLI still select `journal_backend=legacy`,
the original `LocalNoosphere` with its historical
`mojum.noosphere.sqlite/1` source envelope.

## New source journal opt-in

A new, empty PRIVATE SQLite path is required:

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db /private/path/new-v4-segmented.sqlite3 \
  --exploration-mode goal_free --developmental-policy local_cell_v4 \
  --sensory-mode none --journal-backend segmented \
  --journal-segment-size 64 --ticks 120
```

For a CPU-first, foreground CPython/Termux-style host:

```bash
PYTHONPATH=src python -m mojum.portable_host \
  --db /private/path/new-portable-v4-segmented.sqlite3 \
  --developmental-policy local_cell_v4 \
  --sensory-mode none --journal-backend segmented \
  --journal-segment-size 4096 \
  --ticks 120 --interval-ms 1000
```

The `segmented` flag is **only accepted for**
`developmental_policy=local_cell_v4` and is still a synthetic,
strictly bounded, point-room-only organism. The bootstrap pins
`journal_backend=segmented`,
`journal_schema=mojum.noosphere.segmented-sqlite/1` and the exact
immutable `journal_segment_size`. It cannot reopen an old
`LocalNoosphere` journal, nor can legacy mode reinterpret the
new one. Different segment sizes also fail closed.

Only v4's opted-in journal event budget changes from 100000 to
the new backend's **262144 total events**. The v4 typed
`MAX_ABSOLUTE_TICKS=99999` still bounds the *actual number of
motor frames*. This change does not make Mojum run forever.
Other policies and old 100k event limits stay exactly as before.

An opt-in HMAC replay cache still authenticates only its
owner-only plaintext snapshot; it is not the journal itself and
cannot substitute for verifying all source segments. Every
reopen audits all linked journal segments, then may restore a
correctly signed pinned organism state and simulate only the tail.
The cache's bootstrap manifest hash prevents changing the
journal backend, owner, graph, motor/sensor schema or segment size.

## End-to-end tests

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_segmented_aquarium.py -v
```

- **330 actual** v4 synthetic goal-free Aquarium frames, split
  into **six** immutable 64-event logical SQLite segments, with
  a HMAC replay prefix saved at tick 130, 200-frame tail,
  full cold replay and a resumed frame 331.
- Two concurrently opened Aquarium instances: stale
  compare-and-swap writer cannot publish an old state.
- Old source or alternate segment-size reopen is rejected and
  the matching source remains intact.
- Two actual foreground portable-host subprocesses restart
  one canonical new source from tick 3 to tick 6, with an
  opt-in signed owner-private cache, no private frame output.

The separate [#139](https://github.com/Garyu-tt/mojum/pull/139)
data-layer test commits **100001** real synthetic events, closes
and verifies all 25 logical segments, then appends event 100002.
That is a source journal test, **not** 100001 Aquarium motor frames.

No physical multi-file shards, redundancy, online device, GPU,
Android daemon, full neural brain, subjective-experience guarantee
or authenticated immutable external root. A single physical
SQLite source is still a single storage failure domain. Do not
merge to main or deploy without independent durability review
and ancestor PR reconciliation.
