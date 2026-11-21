# Mojum: real synthetic Aquarium journal cold copy (experimental)

This is the first transfer candidate that copies the **actual SQLite Noosphere
journal backing `mojum.aquarium`**, not invented five-component fixture files.
It replays a frozen copy to verify that GENESIS acquired changes, synthetic
world, shadow pulse-learner and event history reconstruct exactly.

This does **not** implement an independent neural brain, physical body,
MuJoCo, durable replicated Noosphere, identity authentication, encryption,
automatic failover, snapshot of a running motor controller, or live migration.
The journal is PRIVATE plaintext. The source and destination folders must be
trusted local storage. Do not upload these files to public GitHub/CI.

## Reproduce with the existing synthetic Aquarium

```bash
# Produce a PRIVATE source journal; the Aquarium CLI can be run repeatedly.
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante-source.sqlite3 --ticks 30 --neural-mode shadow

# Make a new no-clobber SQLite copy from committed pages, including WAL.
PYTHONPATH=src python -m mojum.aquarium_cold_copy export \
  --db ./dante-source.sqlite3 --output ./dante-snapshot.sqlite3
```

The export result is a small JSON object containing the pinned `journal_head`,
`db_sha256`, `tick`, `event_count`, `agent_id` and an explicit
`VERIFIED_SYNTHETIC_REPLAY_NOT_LIVE_MIGRATION` status. It never prints
PRIVATE events or the SQLite contents. **Record the head and checksum on a
separate trusted channel.** These values in an untrusted archive or chat are
not a proof of origin.

Transfer the private file only through a trusted channel. Verify on the other
machine with independently remembered values (example placeholders):

```bash
PYTHONPATH=src python -m mojum.aquarium_cold_copy verify \
  --db ./dante-snapshot.sqlite3 \
  --agent-id dante-fixture-001 \
  --expected-head "<trusted 64-char journal_head>" \
  --expected-sha256 "<trusted 64-char db_sha256>" \
  --expected-tick 30
```

Export and verification use the same trusted GENESIS seed, graph, room,
clock and modes as the original run. The CLI defaults to the packaged seed
and graph, the default room and scripted movement. The journal owner defaults
to the selected seed's `genome_id`. For an opt-in v4 journal
with numeric toy contact, pass `--motor-mode local_cell_v4 --sensory-mode toy_probe_v2`
to both commands. The same rule applies to `--brain-mode`, `--temporal-mode`,
`--neural-mode`, `--genome` and `--graph` when those settings differ. Use the
Python API to pass a custom room, clock or toy scene.

A verified copy can be reopened as an **independent forked local simulation**
using `mojum.aquarium --db ...` with matching settings. That is *not*
coordinated replication: writing to two forks creates divergent histories.

## Actual consistency checks

- Preflights the original journal read-only, takes a consistent SQLite snapshot through a read-only connection, and fully replays **only the temporary copy**. Export never chmods, repairs or bootstraps the original.
- Uses SQLite's **online backup API** rather than copying an open WAL-backed
  `.sqlite3` file via ordinary file copy. No external network is used.
- Pins the temporary copy's committed event head before any writable replay,
  then checks its deterministic Aquarium checkpoint against that recorded head.
  Concurrent source commits may be included in the consistent backup; the
  output identifies that exact snapshot by head, tick and SHA-256.
- Verifies the standalone `.sqlite3` has no required WAL sidecar, computes a
  SHA-256 of the final file and fsyncs the data file. Publishes via hard link
  to a **new** filename only, never replacing an existing path.
- Import verification requires an expected journal head and owning organism ID;
  expected byte SHA and virtual tick are recommended additional gates.
- Source file must exist and be a regular non-symlink file; database pages are
  limited to **128 MB**. No filesystem/network-backed concurrent-directory
  adversary is supported.

## Boundaries

- The source may continue after export. The exported file represents the
  validated point-in-time source view, not the latest state for all eternity.
- SQLite's backup API provides a consistent database copy, **not** cross-service
  atomicity across arbitrary neural, MuJoCo, GPU or Noosphere replicas.
- SHA-256 and the internal journal chain are **not** an authenticated signature;
  independently pinned values help detect substitution. No monotonic rollback
  counter is provided.
- This tool cannot guarantee power-loss durability or directory fsync on every
  OS/filesystem and does not protect hostile parent-directory replacement.
  Hard-link support is required. Verify the destination before use.
- Never restore saved motor gate emergency-stop state, authorization or secrets
  from this fixture. No actuator or host capability restoration occurs.
- A copy is not an encrypted backup; carry privacy controls over to both hosts.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_aquarium_cold_copy.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

CI must pass on the final PR SHA across Python 3.11–3.13, followed by separate
review before any merge. No claims of Pixel 9 or physical-device testing.
