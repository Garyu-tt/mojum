# An authored room, one persistent organism and offline tools

This workflow uses one installed Mojum wheel. After installation, the runtime,
room designer, trace viewer and journal commands work without a source checkout
or network connection. Use Python 3.11 or newer and a local writable directory.
The runtime remains an experimental synthetic fixture.

## Create a room and start the host

Export both standalone interfaces to new local files:

```sh
python -m mojum.room_designer --output ./room-designer.html
python -m mojum.trace_viewer --output ./aquarium-viewer.html
```

Open `room-designer.html` in your browser, adjust the room and export its JSON
as `my-room.json`. Keep that file: its exact geometry and start are pinned into
the organism's journal. Both HTML exports refuse to replace an existing file.

Validate the room, then run a bounded foreground session:

```sh
mojum-room-check ./my-room.json --format json
python -m mojum.portable_host --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --ticks 2 --interval-ms 20
```

The host stops after two committed ticks. These flags select one explicit
fixture profile. For an authored room, this host creates an empty sensory
scene with the same room dimensions. Later replay commands therefore need
`--sensory-scene empty` in addition to the same room and modes. The host itself
continues to select that scene through `--room-file`.

## Inspect and advance development

```sh
python -m mojum.aquarium_development status --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --sensory-scene empty
```

The receipt reports the committed `journal_head`, physics tick, event count
and developmental age. Copy that head into `HEAD_FROM_STATUS` below:

```sh
python -m mojum.aquarium_development advance --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --sensory-scene empty \
  --months 6 --expected-head HEAD_FROM_STATUS
```

This adds one developmental event: 72 → 78 synthetic months. Physics time,
world state and neural state do not advance. Body previews use the stored age.
The receipt contains a new head; reuse of the older head is rejected, so a
retry cannot silently repeat the milestone.

Restart the same host with the new `HEAD_FROM_ADVANCE`:

```sh
python -m mojum.portable_host --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --ticks 2 --interval-ms 20 \
  --expected-head HEAD_FROM_ADVANCE
```

It cold-replays the milestone and continues to tick four with six journal
events. Keep the `journal_head` from this final host receipt for the export.

## View the run and verify a backup

Export the committed frames to a new local file, explicitly opting into
PRIVATE data export:

```sh
python -m mojum.aquarium_trace_export --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --sensory-scene empty \
  --expected-head HEAD_FROM_FINAL_HOST --allow-private-export \
  --output ./organism.private.jsonl
```

Open `aquarium-viewer.html` locally and choose `organism.private.jsonl` in its
file picker. The viewer checks the export's integrity metadata and displays
the four recorded physics frames. The developmental event is preserved in
the journal; it does not create an extra physics frame. The viewer is an
observer and does not run or modify the organism.

Create a cold copy and preserve its returned head, tick and `db_sha256`:

```sh
python -m mojum.aquarium_cold_copy export --db ./organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --sensory-scene empty \
  --output ./organism.backup.sqlite3
python -m mojum.aquarium_cold_copy verify --db ./organism.backup.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --temporal-mode range_guided --sensory-scene empty \
  --expected-head HEAD_FROM_COPY --expected-tick 4 \
  --expected-sha256 SHA256_FROM_COPY
```

The cold copy includes the milestone and cold-replays to the original final
checkpoint. Verification reads the copy without advancing it. Keep the
journal, backup and PRIVATE trace local, together with the matching room and
any custom genome or graph files needed to replay them.

## Configuration boundaries

The same workflow supports `toy_probe_v2` with `--temporal-mode none`, and
`shadow` neural mode or `developmental_controller` brain mode when those flags
are repeated consistently. `--sensory-scene empty` requires a toy sensory mode.
Omitted or explicit `default` preserves the built-in toy scene; neither choice
is inferred from journal contents. Wrong room, modes, scene or expected head
is rejected. Existing arbitrary custom toys or nondefault virtual clocks
still require their matching Python configuration.

Only `scripted` and `developmental_fixture` admit developmental advances.
Each explicit step is 1–12 whole months, with a maximum of 192 synthetic
months. Elapsed wall time does not age the organism. See the
[development command](AQUARIUM_DEVELOPMENT_CLI_R01.md) for input, live-WAL,
concurrency and interrupted-receipt behavior, and the
[scene selector](CLI_SENSORY_SCENE_R01.md) for the replay contract.

## Verification

`tests/test_installed_aquarium_workflow.py` runs real host subprocesses for
4×4 and 5×4 rooms, both toy sensor versions and both neural modes. It checks
developmental state, stale-head refusal, unchanged runtime state, host resume,
trace contents and exact cold-copy replay. Wrong or invalid scenes are tested
without changing closed journal bytes or permissions; default-scene journals
remain compatible.

The installed-wheel smoke exports both complete HTML interfaces and runs the
same workflow tests with isolated installed Python subprocesses, outside the
checkout in a loopback-only network namespace. Canonical asset equality and
the existing real-browser gates remain separate checks of the offline UIs.
