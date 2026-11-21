# Aquarium R0.4 — cold replay from an actual synthetic journal

This feature builds on five-component checkpoint transport in PR #48
(stacked on #46 and #44). It captures a synthetic Aquarium's committed
Noosphere journal, GENESIS acquired changes, shadow predictor weights
and exact point-room world state.

This is not live migration of a running organism. The source Aquarium must
be quiescent: no concurrent writer. The journal includes PRIVATE events.
Protect and transfer the archive only over a channel appropriate for private data.

## Device A — run and export a fixture

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante-source.sqlite3 --ticks 30 --neural-mode shadow

    PYTHONPATH=src python -m mojum.cold_aquarium export \
      --db ./dante-source.sqlite3 \
      --genome ./examples/genesis_seed.json \
      --graph ./examples/first_loop.json \
      --organism-id dante-fixture-001 \
      --output ./dante-cold-r30.mojum.json

The exporter reopens and deterministically replays source history before
capturing. Standard output includes the committed tick and manifest_sha256,
but not the private events. The portable archive DOES include PRIVATE events
and learning state as unencrypted Base64 bytes. Do not publish the archive.

The memory component is limited to 4 MB; the source journal has a maximum of
10,000 events in this fixture. Exceeding a budget fails closed.

## Device B — verify and cold restore into a fresh journal

Record the trusted manifest SHA-256 from device A via a separate trusted
channel. Do not read the expected digest from the archive being imported.

    TRUSTED_MANIFEST_SHA="paste-64-character-lowercase-hex-value"

    PYTHONPATH=src python -m mojum.cold_aquarium restore \
      --archive ./dante-cold-r30.mojum.json \
      --db ./dante-restored.sqlite3 \
      --organism-id dante-fixture-001 \
      --tick 30 \
      --manifest-sha256 "$TRUSTED_MANIFEST_SHA"

    PYTHONPATH=src python -m mojum.aquarium \
      --db ./dante-restored.sqlite3 --ticks 5 --neural-mode shadow

The importer validates canonical JSON, each component hash, organism ID,
pinned tick, and optionally a separately trusted manifest fingerprint before
creating its temporary SQLite staging database. It then appends every private
event through Noosphere CAS, checks each hash plus the final chain head, and
replays the whole state through the existing Aquarium implementation.

It compares checkpoint, GENESIS acquisitions, online predictor state, and
world revision/coordinates to the original. Only after passing all checks
does it publish a new database filename without overwriting an existing one.

Original room numeric types are preserved: deterministic world hashes
distinguish integer 4 from floating 4.0 even when the room manifest renders
both dimensions numerically equivalent.

## Boundaries and safety

- A paused single-writer source is required. Before/after head and checkpoint
  comparison detects straightforward concurrent changes but is not a
  cross-component atomic barrier or a concurrent-writer lock.
- The shadow predictor is a small synthetic online heartbeat learner, not a
  complete brain. The room is a point fixture, not a MuJoCo simulation.
- Body data intentionally contains NOT_CAPTURED_ACTUATORS_DISABLED. Import
  never grants actuator capabilities or resets emergency stop.
- Archives have no built-in encryption, signatures, identity certificates,
  peer authorization, transport encryption, anti-rollback counter or quorum.
- Internal SHA-256 checks ensure consistency only. A digest obtained by a
  separate trusted channel can detect substitution relative to that digest,
  but does not identify the author of the archive.
- The destination parent must be trusted and hard-link-capable; there is no
  guarantee of no-replace behavior against a hostile concurrent directory
  writer, nor of durable recovery after arbitrary power loss.
- This is a cold data restore, not live process migration or continuous
  failover. The receiving machine needs compatible Mojum code and CPU.
- Never commit actual private organism history to this public repository.

## Regression

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_cold_aquarium.py' -v
    PYTHONPATH=src python -m unittest discover -s tests -v

Tests include PRIVATE journal continuity, learned weights, acquired genes,
deterministic next-step equality, two independent processes, an empty-history
boot, disabled neural mode, mismatched identity/revision, modified archive,
trusted digest mismatch and destination no-overwrite.

Next: a source-side quiescence barrier with an explicit transactional revision
and device-identity/authorization protocol before attempting actual
multi-device replication or automatic recovery.
