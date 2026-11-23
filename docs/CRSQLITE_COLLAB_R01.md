# Mojum #698 CR-SQLite public annotation scratchpad (R0.1, experimental)

## Isolation and use

This is the **independent CR-SQLite research sub-slice** of #698. The NATS
JetStream transport work is owned by another integration worker. `mojum.crsqlite_collab`
uses the upstream CR-SQLite SQL extension API to replicate a **disposable,
PUBLIC_SYNTHETIC-only, 1-table annotation scratchpad**. It does not read any
Mojum SQLite files. The SQLite connection is always `:memory:` and cannot be
configured to point to Noosphere/organism journals. The only table is
`collab_notes(note_id TEXT PRIMARY KEY NOT NULL, note TEXT NOT NULL)`.

The scratchpad is deliberately non-authoritative. Annotations are **not**
genomes, node definitions, revisions, motor commands, runtime configuration,
memories, audit/QA verdicts or accepted code. Nothing is automatically
propagated to live graph state. This module does not implement a peer discovery,
trust, signing, network transport, authentication, durable history, backups,
or a production conflict policy. `trusted_local_fixture=True` means only
**manual offline fixture exchange**. It is not a security boundary or
an authorization token. Do not expose `apply()` to network input.

## Upstream, licensing and pin

- Code: https://github.com/vlcn-io/cr-sqlite
- SQL API: https://vlcn.io/docs/cr-sqlite/api-methods/crsql_changes
- Installation/finalization: https://vlcn.io/docs/cr-sqlite/installation
- Candidate native release **v0.16.3**: https://github.com/vlcn-io/cr-sqlite/releases/tag/v0.16.3
- Upstream repository says **MIT** (2023 One Law LLC). Inspect the exact
  binary's license, transitive dependencies and platform compatibility before
  distributing or executing it. Mojum's catalog still says `planned`.
- CR-SQLite v0.16.1+ requires non-null site IDs and non-null primary keys.
  This bridge requires a 16-byte site ID and a non-null text primary key.
- Builds **must be independently pinned to the v0.16.3 release**, recorded
  with an SHA-256 and supplied by the operator. The module enforces the given
  digest, but **does not attest the claimed release version**. No binary is
  bundled, downloaded, installed or automatically enabled.

## Offline hermetic verification (no native library)

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_crsqlite_collab*.py' -v
```

The default suite checks strict packet encoding/decoding, no PRIVATE envelope,
SQL placeholder use, all-or-nothing application and rollback, replay packet
stability, path/checksum gating, hard budgets, and default-disabled operation.
The fake SQL connection **only records which APIs would be called**; it does
not simulate a CRDT and does **not prove native convergence**.

## Optional real CR-SQLite test (no network)

Use a trusted OS-isolated test host with an operator-installed, release-pinned
native CR-SQLite v0.16.3 library. Extension loading is executable native code.
Create or obtain the native library yourself and verify its checksum. Example:

```sh
export MOJUM_CRSQLITE_EXTENSION=/absolute/trusted/path/crsqlite.so
export MOJUM_CRSQLITE_SHA256=$(sha256sum "$MOJUM_CRSQLITE_EXTENSION" | cut -d' ' -f1)
PYTHONPATH=src python -m unittest tests.test_crsqlite_collab_native_optional -v
```

On macOS/Windows use a `.dylib`/`.dll` and the equivalent digest command.
The optional native test is **SKIPPED** without both environment variables.
It makes two independent in-memory peers, writes distinct notes during a
synthetic partition, exchanges `crsql_changes` packets in both directions,
and checks convergence and idempotent retransmission. Do not equate this tiny
fixture with guaranteed convergence under arbitrary concurrent edits,
reordering, crashes, or multiple native versions; those require further
independent acceptance and benchmarks.

## API

```python
from mojum.crsqlite_collab import open_scratchpad

with open_scratchpad(
    enabled=True, classification='PUBLIC_SYNTHETIC',
    extension_path='/absolute/path/to/crsqlite.so',
    expected_sha256='<64 lowercase SHA256 hex characters>',
) as a:
    a.put('sketch_note_1', 'Public synthetic layout annotation')
    packet = a.export(since=0)
    # Send only to a separate, manually approved LOCAL fixture.
    # A different scratchpad may call .apply(packet, trusted_local_fixture=True).
```

The packet is a bounded, canonical JSON representation of nine upstream CRR
change columns: table, packed PK, cell/column ID, value, column clock,
database clock, site ID, causal length, sequence. Binary IDs use canonical
base64. Import validates the entire packet before beginning an explicit
SQLite transaction, uses SQL bind parameters, and rolls back failures.
The import refuses any table other than `collab_notes`, any change cell outside
`note` or the CR-SQLite deletion sentinel `-1`, arbitrary binary cells, and
non-public envelope types. This strict subset intentionally may reject newer
upstream features; no auto-upgrade is allowed.

## Resource ceilings and failure modes

| Budget | Bound |
| --- | --- |
| SQLite storage | In-memory only, never a file journal |
| Rows | 64 current notes; byte-limited note content |
| Annotation content | 512 UTF-8 bytes per note |
| Changes per exchanged packet | 128, no partial export |
| Packet size | 64 KiB maximum |
| Packed primary key | 128 bytes |
| Native extension | Local, absolute path, regular non-symlink file, at most 32 MiB, exact operator-supplied SHA-256 |
| CPU / latency | **Not measured** with real native CR-SQLite; must benchmark before any adoption |

Failures are fail-closed: bad envelope, excess bounds, invalid SQLite clock,
unknown table or column, unsupported extension, checksum mismatch, closed
scratchpad, or unauthorized merge raise an exception. The on-disk executable
could be replaced between checksum and `dlopen` (TOCTOU); file restrictions do
not constitute a sandbox. Native extension bugs may crash the process.
Require a separately sandboxed worker, authenticated peer identity, sequence
and replay policy, tested conflict rules, resource limits and independent QA
before contemplating any remote integration or PRIVATE metadata.

This code **does not** change the existing canonical Universal Graph IR,
Noosphere memory, real motor gate, production hosts, JetStream workers, CI
requirements, or the release gate. Draft PR is the handoff point.
