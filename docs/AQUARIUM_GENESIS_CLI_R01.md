# Installed Aquarium GENESIS command

`python -m mojum.aquarium_genesis` exposes the existing single-event
`Aquarium.acquire` transition. It works from an installed wheel with packaged
seed and graph defaults; a source checkout is not required. This is a synthetic
integer-parameter fixture, not biological adaptation or evidence of learning.

## Boot, inspect, acquire, resume

Create a journal with the ordinary installed command:

```sh
python -m mojum.aquarium --db organism.sqlite3 --ticks 2
python -m mojum.aquarium_genesis status --db organism.sqlite3
```

Copy the `journal_head` from the verified status receipt into `HEAD`, then append
one explicitly described acquisition:

```sh
python -m mojum.aquarium_genesis acquire --db organism.sqlite3 \
  --expected-head HEAD --event-id synthetic-gain-001 \
  --gene heartbeat_bpm --delta 15 \
  --source synthetic-experiment --evidence "Explicit synthetic parameter trial"
```

`HEAD` is a placeholder for the exact 64-character lowercase digest, not a
literal argument. Acquisition is refused if the journal has advanced. Copy the
new `journal_head` from the acquisition receipt into `NEW_HEAD` and resume:

```sh
python -m mojum.aquarium --db organism.sqlite3 --ticks 1 --expected-head NEW_HEAD
python -m mojum.aquarium_genesis status --db organism.sqlite3
```

With the packaged seed this records the acquisition at tick 2, adds one event
without advancing that tick, and changes the heartbeat parameter from 60 to 75
for the next frame. The resumed journal reaches tick 3 with five events. The
original inherited seed is unchanged. Cold replay reconstructs the same acquired
ledger and phenotype through the canonical Aquarium code.

`--event-id` must be unique within the acquired ledger. `--gene` accepts
`heartbeat_bpm`, `body_scale_milli`, or `sensory_gain_milli`; `--delta` uses the
existing integer units of that gene. Canonical GENESIS checks the delta and all
effective phenotype bounds, including previously scheduled future changes.

`--valid-from-tick` defaults to the current verified tick. An explicit current
or future tick schedules the effect without changing previous frames.
`recorded_at_ns` is always derived from the verified virtual clock.
`--observed-at-ns` defaults to that same time and may be explicitly earlier,
but cannot be negative or later. Source and evidence are required,
caller-declared provenance, not authenticated observations.

## Preserve the existing replay profile

Both subcommands accept the existing `--genome`, `--graph`, `--room-file`,
`--neural-mode`, `--motor-mode`, `--brain-mode`, `--sensory-mode`,
`--temporal-mode`, `--pulse-sum`, and `--dt-ns` selectors. Supply the same inputs
and profile used at birth; a mismatch is rejected, never inferred from private
records. Defaults match the ordinary scripted Aquarium.

For an authored-room host that pinned an empty scene, include all its profile
flags and `--sensory-scene empty` on **both** `status` and `acquire`. For example:

```sh
python -m mojum.aquarium_genesis status --db authored.sqlite3 \
  --room-file room.json --motor-mode developmental_fixture \
  --sensory-mode toy_probe --temporal-mode range_guided --sensory-scene empty
```

The explicit empty scene uses the authored room's width and height, including
non-default rooms such as 5×4. The same scene is passed to the read-only replay
and the writable reopen. `--sensory-scene default` preserves the ordinary toy
scene. Empty scenes require a toy sensory mode; arbitrary caller-defined toy
layouts are not represented by this CLI. The command uses the shared scene
selector already accepted with the installed host/lifecycle workflow.

Status can inspect supported local-cell histories. Acquisition is limited to
the canonical `scripted` and `developmental_fixture` modes because local-cell
compatibility histories prohibit acquisition events.

## Admission, privacy and concurrent writers

Status opens an existing Aquarium read-only and performs deterministic replay.
Acquisition first does the same replay, validates the proposed event against a
detached canonical ledger, checks the remaining journal budget and obtains the
verified anchor. Only then does it reopen with that exact expected head and
call `Aquarium.acquire`. The existing CAS append rejects a writer that races
after reopen. There is no retry, silent head refresh, multi-event transaction,
new journal creation or schema migration.

Malformed, duplicate, out-of-bounds, retroactive and stale requests produce a
usage error with no success receipt. Missing, empty and foreign files are not
initialized. Status and failed preflight do not append application events or
change the main database's content or permissions. As with the existing
read-only Aquarium API, SQLite can create or use WAL coordination sidecars;
this is not a byte-for-byte forensic read of the entire directory. Use a trusted
local directory; the anchor is a checksum and does not authenticate a writer
or defend against arbitrary same-user pathname replacement.

Receipts contain only identity, tick/time, journal and genome hashes, event and
acquisition counts, plus the prior journal head for acquisition. They do not
print genes, parameter values, event IDs, source/evidence text, private frames,
or learner state. Unicode identities are escaped for legacy output encodings.
The underlying journal remains PRIVATE and unencrypted. Command-line source
and evidence can be visible in local shell history or process inspection; do
not put secrets in them.

A successful append occurs before its receipt is printed. If a process is
interrupted afterward, or another writer advances before receipt validation,
inspect the journal before retrying; the CLI never repeats an append
automatically. Duplicate IDs and expected-head checks remain authoritative.

## Verification

`tests/test_aquarium_genesis_cli.py` exercises real CLI boot/acquisition/resume,
current and future effects, cold replay, unchanged runtime state, empty/foreign
files, stale anchors, both writer-race windows, bounds and duplicate IDs,
authored empty scenes, non-default clocks and Unicode. The tests also run
against an installed wheel outside the source tree. Genome files are regular,
bounded to 64 KiB, decoded as strict UTF-8 and reject duplicate JSON keys;
graph and room inputs use their existing bounded loaders.
