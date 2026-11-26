# Noosphere R0.1: offline read-only integrity auditor (#929)

A narrowly scoped, stdlib-only acceptance tool for one **existing** local
Noosphere SQLite journal. It does not export event data, create or repair
journals, perform backup or replication, and has no network dependency.

## Usage

From the repository root with Python 3.11+:

    PYTHONPATH=src python -m mojum.noosphere_audit \
      --db ./agent_PRIVATE.sqlite3 --agent-id synthetic-dante

For stronger tamper evidence, obtain the exact *previously recorded* SHA-256
journal head through an independent trusted channel or device, then supply:

    PYTHONPATH=src python -m mojum.noosphere_audit \
      --db ./agent_PRIVATE.sqlite3 --agent-id synthetic-dante \
      --expected-head 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

The example digest is a placeholder, **not** a valid trusted anchor. The caller
must substitute the known-good head of *their own* journal. An anchor cannot
prove authenticity if obtained from the same possibly rewritten database.

Successful output is one deterministic JSON line with:

- mojum.noosphere.audit/1 schema, PASS status, count and last journal head
- expected_head_verified = true only when an external head matched
- explicit LOCAL_CHAIN_ONLY_NOT_AUTHENTICATED or
  EXTERNAL_HEAD_MATCHED_NOT_A_SIGNATURE limitation
- contents = NOT_EXPORTED, writes = NOT_PERFORMED

No payload, event ID, owner or topic is printed. The SHA head and count are
metadata, **not anonymized secrets**; choose where to publish them carefully.

## Fail-closed behavior

- Exit 0: the entire chain matches its canonical events and optional anchor.
- Exit 1: invalid/missing/foreign SQLite journal, mismatched anchor, corrupt
  event, inaccessible file, symlink path, oversized wire or any audit failure.
  Only a fixed error code is printed; no source exception or PRIVATE payload.
- Exit 2: argparse usage error (required flag missing, unrecognized option).

All rows are checked by the existing LocalNoosphere.verify_chain(strict_wire=True)
implementation inside one read transaction. Before JSON parsing, the auditor
rejects any stored wire larger than 128,000 UTF-8 bytes. It also refuses
parent traversal and symlinked existing path components. It opens the SQLite
connection in read-only mode and does not execute writes.

The read transaction pins a consistent snapshot while a separate writer
appends. SQLite WAL metadata/sidecar handling remains SQLite's responsibility;
this tool guarantees no writes to Noosphere tables, **not** that an arbitrary
host filesystem can never create or touch an SQLite sidecar. The operator
should keep journal directories private.

## Security limitations

A valid local SHA-256 chain **is not authenticated** without a trusted external
head. A malicious party with local write access could forge the database and
its chain. Even an external digest does not provide a digital signature,
quorum, encryption, remote storage, or witness provenance.

This path check is not hardened against a malicious concurrent process
swapping directory entries between preflight and open. Operate on trusted
directories, ideally a cold copy under control of the owner, when facing a
hostile filesystem. This audit does not replace independent Noosphere backup
and restore verification (#693, #141).

## Synthetic tests

    PYTHONPATH=src python -m unittest tests/test_noosphere_audit.py -v

The fixture tests check a clean local chain, the absence of PRIVATE data in
reports, independent expected-head matching/mismatch, wrong owners, missing
files, ancestor symlinks, noncanonical and corrupted rows, a huge malformed
wire rejected before Python JSON parse, empty journals and repeatable
read-only database bytes. No production journal is used.
