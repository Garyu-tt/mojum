# CR-SQLite collaborative metadata isolated spike (issue #698)

Status: REVIEW-ONLY, opt-in experiment, not accepted as a continuity transport or a Noosphere authority.

## Scope

This branch studies the independent CR-SQLite slice of issue #698. NATS JetStream transport is owned by another worker. Only synthetic, explicitly PUBLIC, ASCII collaborative note labels exist here. The two replicas use SQLite :memory: databases; both export and import refuse ANY file-backed/attached SQLite database. No canonical Aquarium journal, organism histories, PRIVATE Noosphere records, actual host identities, credentials, network client, or actuator interface is touched. The only table admitted to the changeset is collab_notes and the only replicated column is value.

Module: src/mojum/crsqlite_metadata_spike.py.

The typed mojum.crsqlite.public-metadata-changes/1 test envelope includes CR-SQLite's table, pk, cid, val, col_version, db_version, site_id, cl and seq fields. It is NOT authenticated, signed, encrypted, causally complete or network-ready. It must not be connected to NATS or any real peer without a separate security and schema review. Even an ASCII string may contain sensitive information: do not call these validation checks a DLP filter.

## Offline stdlib checks (no extension needed)

    PYTHONPATH=src python -m unittest tests.test_crsqlite_metadata_spike -v

The test suite covers round-trip, schema versioning, strict allowlisted fields/table/column, binary length/counter checks, malformed or duplicate JSON, oversized/truncated batches, no SQL mutation on bad input, fixed parameterized SQL, file-backed DB refusal, and local native binary hash/path refusal. No native convergence is claimed by these tests.

## Optional actual CR-SQLite run

Upstream: https://github.com/vlcn-io/cr-sqlite , MIT (upstream LICENSE, copyright One Law LLC). Candidate tag: v0.16.3. The upstream v0.16.1+ change-stream site_id is required, not NULL. The code requires the v0.16.2+ crsql_sha() function. A release tag alone does not attest a binary: source/binary license and SHA-256 must be independently reviewed. Mojum's own integration catalog stays planned and license_review=required.

Build or obtain the correct native loadable extension yourself from a trusted source; this PR does not install, fetch or bundle it. Pin its SHA-256 from an independently reviewed trustworthy artifact, not merely a checksum calculated from an unknown download. Then explicitly run:

    PYTHONPATH=src python -m mojum.crsqlite_metadata_spike \
      --execute --extension /absolute/reviewed/path/crsqlite.so \
      --sha256 64_lowercase_hex_sha256_from_trusted_source

The CLI refuses execution without all three flags and rejects relative paths, symlinks, non-regular files, extension files larger than 128 MiB, changing files and mismatched SHA-256. Python's sqlite3.load_extension executes arbitrary native code once opted in. A digest pin alone does not prevent a privileged attacker replacing files between verification and execution; run in a separate unprivileged sandbox. On Windows use a reviewed crsqlite.dll.

The actual test attempts concurrent conflicting edits of shared and disjoint keys in two in-memory replicas, exports each replica's CR-SQLite changes, imports them twice to test duplicates, retries two more reconciliation rounds, then compares both sorted snapshots and requires all three keys. It prints JSON only after convergence. Failure is nonzero. This is a functional demo, not a global convergence proof, partition test, production benchmark or multi-writer journal acceptance.

## Adoption and resource gates

- Default Mojum Python baseline: no added dependencies, no auto-load, no auto-install, no network.
- Work is bounded to 2 in-memory connections, 256 changes per export, 64 KiB per wire packet, 64 characters per note label and SHA-256 verified native binary size <= 128 MiB. Native CPU/RAM are not automatically limited by these logical bounds. For untrusted binaries use a separate CPU/RAM/time-limited process and inspect the upstream supply chain.
- Later QA must benchmark CPU/RAM wall time, compare actual v0.16.3 extension behavior on Linux and Windows, test partial network partitions, deletes, clock skew and malformed native rows. Add authenticated enrollment, fencing, causal replay, transport backpressure and encryption before any remote messaging.
- No CRDT result may become the authority for organism identity, journal sequence, policy state, snapshots or Noosphere PRIVATE records. The canonical SQLite single-writer model and explicit recovery anchors remain unchanged.

Upstream API references: https://vlcn.io/docs/cr-sqlite/quickstart and https://vlcn.io/docs/cr-sqlite/installation .
