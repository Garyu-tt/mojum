# R0.17 / PRIVATE physical sealed-segment archives (research Draft)

This independent feature is stacked on
[PR #140](https://github.com/Garyu-tt/mojum/pull/140);
[Issue #141](https://github.com/Garyu-tt/mojum/issues/141)
tracks the remaining real distributed durability and replication work.

No automatic export or migration of original PRIVATE histories.
Existing LocalNoosphere, SegmentedNoosphere, Aquarium, and portable
host default contracts stay byte-identical.

## What is actually implemented

`mojum.segment_archive.export_sealed_segments` writes individual
**physical files** to a pre-existing owner-only (0700) local directory:

- `segment-00000000.events.jsonl` — the original canonical PRIVATE
  Noosphere event envelopes, their global absolute sequence numbers
  and each original segmented event hash.
- `segment-00000000.manifest.json` — a **separate immutable** 0600
  metadata file with schema `mojum.noosphere.physical-segment-archive/1`,
  agent ID, fixed segment size, first/last seq, event count, prior
  segment's head, this segment's final event head, SHA-256 of the
  physical JSONL payload, byte count and visibility flag.

Only **FULL sealed segments** are copied. If a source has 130
events with a 64-event segment size, 128 events are exported and
**2 unsealed live tail records remain exclusively in the source**.
The result explicitly reports the missing tail; it does not claim
a full backup of the organism.

The PRIVATE export requires an explicit
`include_nonpublic=True`. Its default is to refuse to export
rather than silently publish personal history. The archive is
**plaintext** (not encrypted), so it must remain on an authorized
private volume; never upload to a public GitHub artifact.

Data file is fsynced and atomically linked without overwriting
existing files; only afterwards is its immutable manifest similarly
published. A crash between those steps leaves an orphan data file;
a retry refuses it for operator quarantine instead of rewriting the
source or making up a manifest. Source events are never deleted.

## Explicit offline CLI (metadata-only output)

The new `mojum.segment_archive_cli` runs on ordinary CPython
without a GPU or remote API. It never prints PRIVATE event payload,
genome state, file paths or HMAC secrets.

Example for a **new synthetic** segmented database already written
and independently trusted source head `SOURCE_SHA256`:

```bash
PYTHONPATH=src python -m mojum.segment_archive_cli export \
  --db /private/synthetic-segmented.sqlite3 --agent-id dante-fixture-001 \
  --segment-size 4096 --archive-dir /private/archive-A \
  --expected-source-head SOURCE_SHA256 --include-private

PYTHONPATH=src python -m mojum.segment_archive_cli verify \
  --archive-dir /private/archive-A \
  --expected-sealed-head INDEPENDENTLY_RECORDED_SEALED_SHA256

PYTHONPATH=src python -m mojum.segment_archive_cli restore \
  --archive-dir /private/archive-A \
  --new-db /private/NEW-restored-sealed-prefix.sqlite3 \
  --expected-sealed-head INDEPENDENTLY_RECORDED_SEALED_SHA256
```

The archive directory must exist already with 0700 permissions
and cannot be a symlink. Do not put unencrypted PRIVATE history
in a public volume. The expected source head and sealed-prefix
head are **different** if an unsealed tail remains. The latter
must come from a separately trusted record, not blindly copied
from a possibly compromised manifest.

## Verification without the SQLite source

`mojum.segment_archive_verify.verify_sealed_archive(directory,
external_expected_head=...)` reads only the private physical files
and validates:
- filenames, complete prefix indices, owner and fixed size;
- strict canonical UTF-8 JSON, duplicate-field rejection,
  valid per-event schemas and global absolute seq;
- each hash-chained original event, every segment's previous/next
  head, and SHA-256 of each physical payload;
- exact declared byte size and PRIVATE classification;
- comparison with a **caller-supplied independently trusted
  sealed-prefix head**.

A manifest or self-supplied checksum does not authenticate itself.
An attacker who replaces the entire archive and the trusted root
can forge a new history. The experimental export does not provide
encryption, signatures, secure remote transport or anti-rollback.

`restore_into_new_sqlite(...)` verifies the archive before
writing, reconstructs all sealed records in a NEW scratch SQLite
file, re-verifies its chain against the independent head, closes
the store, and atomically publishes a new destination without
overwriting any existing database. It never repairs, deletes,
overwrites or silently migrates old source files.

A sealed-prefix restore **cannot reconstruct an unsealed tail**;
availability/loss prevention requires additional replication of
in-progress journal events, sealed segment shipping and external
acknowledgements. This is an explicit R0.17 limitation.

## Repeatable tests

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_physical_segment_archive.py -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_physical_segment_archive_100k.py -v
```

Unit fixture: 130 committed synthetic PRIVATE events, exactly
two full sealed 64-event segments, source made unavailable via
renaming **only a disposable test SQLite**, independently restored
128-event source with matching head. Includes default-private
export refusal, 0700 directory requirement, two manually selected
archive locations, idempotence, missing data/manifest, corrupt
payload, wrong trusted head, overwrite refusal, symlink and a
fault injected after data publish but before manifest creation.
**Two locations in one temp filesystem are NOT two independent
physical failure domains**.

Heavy fixture: **102,400 real distinct synthetic SQLite events**
in **25 full 4096-event segments**, archived to 25 physical data
files and 25 manifest files. Original disposable SQLite is made
unavailable, all 102,400 events are independently reverified and
reconstructed in a new SQLite source whose final head equals the
original independently pinned head. PRIVATE projection remains
invisible by default.

This demonstrates archive data-layer restore, not 102,400 actual
Aquarium motor frames; v4 still has finite absolute-tick budget.
It is a CPU-only off-line prototype without Android/device or
cloud connector assumptions.

## Release blockers

1. Source-to-archive replication + **two real independent
   devices/volumes** and independently authenticated root storage.
   Two folders on one disk do not make a resilient backup.
2. Explicit finalization/replication of the live partial segment;
   crash between flush and upstream acknowledgement; write-fence and
   network partition recovery without automatic fork merge.
3. Encrypted-at-rest archives, signed manifests and key rotation.
4. Closed-world review of filesystem race, journal atomicity and
   disk-full behaviors, with actual power-cut testing.
5. CPU/Termux limits, DR drill, and direct-main ancestor
   reconciliation with peer owners.

Keep Draft, no main merge, original PRIVATE user databases,
production VMs or external destinations touched.
