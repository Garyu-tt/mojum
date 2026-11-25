# Inspect and advance an existing Aquarium's development

The installed package provides `python -m mojum.aquarium_development` for the
runtime's existing journaled developmental age. It works offline with the
Python standard library and bundled default genome and graph.

`status` reads an existing committed journal without advancing it. `advance`
appends one explicit developmental milestone through `Aquarium.advance_development()`.
It does not run a physics tick, rename the organism or create a journal.

## Start, inspect, advance, resume

Create a synthetic organism with the ordinary Aquarium command, then inspect
its development:

```sh
python -m mojum.aquarium --db ./organism.sqlite3 --ticks 2
python -m mojum.aquarium_development status --db ./organism.sqlite3
```

The status JSON contains `development_months`, `tick`, `stored_events`,
`journal_head` and `manifest_sha256`, plus the receipt schema and operation.
It omits the journal path, personal name, genome and learned state.

Copy the exact `journal_head` from that receipt in place of
`SHA256_FROM_STATUS` below:

```sh
python -m mojum.aquarium_development advance --db ./organism.sqlite3 \
  --months 6 --reason "explicit synthetic semester" \
  --expected-head SHA256_FROM_STATUS
python -m mojum.aquarium_development status --db ./organism.sqlite3
python -m mojum.aquarium --db ./organism.sqlite3 --ticks 1
```

A new fixture starts at 72 developmental months. This example changes it to
78 months at the same physics tick and adds exactly one event. A subsequent
Aquarium run cold-replays the milestone and continues normally. Existing body
previews use the stored age when no age override is supplied.

Each advance requires 1–12 whole months, a nonempty reason of at most 256
characters and an explicit expected head. The reason is stored privately in
the journal; it is not included in the receipt. Development stops at 192 months
(16 synthetic years). This is an explicit fixture schedule, not biological
ageing, elapsed wall time or evidence of learning or cognition.

## Match the journal's configuration

Repeat the same `--genome`, `--graph`, `--room-file`, `--neural-mode`,
`--motor-mode`, `--brain-mode`, `--sensory-mode`, `--temporal-mode` and
`--pulse-sum` selections used by the matching Aquarium runtime. Defaults match
the default Aquarium fixture; they do not infer the configuration of an
arbitrary journal. Custom genome and graph files must be regular local files;
the genome input is bounded to 256 KiB and rejects duplicate JSON keys.

For example, a sensorless custom room can use:

```sh
python -m mojum.aquarium --db ./room-organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled --ticks 2
python -m mojum.aquarium_development status --db ./room-organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled
python -m mojum.aquarium_development advance --db ./room-organism.sqlite3 \
  --room-file ./my-room.json --neural-mode disabled \
  --months 1 --expected-head SHA256_FROM_STATUS
```

Advances support `scripted` and `developmental_fixture` motor modes. The
existing local-cell and archived policy contracts do not admit development
events: their status can be inspected with matching selectors, but an advance
is rejected. Built-in toy scenes are supported with their original sensory and
temporal modes. Journals created through Python with a custom toy scene,
nondefault virtual clock or a room/start unavailable through `--room-file`
still require their matching Python configuration. This command does not
substitute a scene or reinterpret a stored manifest.

## Failure and concurrency behavior

Missing files, directories, symlinks, empty files, unrelated SQLite databases,
invalid journals and mismatched configurations are rejected before writable
access. Both operations first replay the existing journal read-only. An
advance also checks the age and event budget before reopening for writing.
Read-only status does not append events or rewrite main database pages. SQLite
may create or update its `-wal` / `-shm` coordination files while reading a WAL
journal. The command uses the real committed WAL state; it does not pretend
the database is immutable or ignore recent committed pages.

The writable reopen is bound to the verified head, and the canonical API's
compare-and-append fences a concurrent writer after that reopen. A stale
expected head cannot repeat an already committed advance. Inspect the current
state before deliberately retrying; a head checksum is a consistency anchor,
not authentication. These operations do not coordinate a distributed writer.

Success returns exit 0 and an ASCII-safe JSON metadata receipt. Rejected input
or journal consistency returns exit 2 without a success receipt. SQLite access
failures return exit 1. As with any local CLI, interruption after SQLite commits
can prevent delivery of stdout. A competing writer after this action's commit
can also cause the final checkpoint check to fail. A missing success receipt
therefore does not prove that no milestone committed: inspect the journal
before deciding on another advance. The original expected head prevents an
automatic repeat. Keep the journal and its reason text private.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_development_cli.py -v
```

The tests use real SQLite journals and subprocess CLIs, including cold replay,
body-preview growth, normal runtime continuation, nondefault configurations,
stale and concurrent writers, byte-preserving rejection and a real SQLite
writer lock. Exact byte/directory checks use closed DELETE-mode fixtures;
a separate live-WAL test verifies visibility of the freshest committed head
while preserving database and WAL event bytes. Running the same file with a
freshly installed wheel's isolated
interpreter also isolates its subprocesses from the source checkout:

```sh
/path/to/fresh/venv/bin/python -I /path/to/source/tests/test_aquarium_development_cli.py -v
```
