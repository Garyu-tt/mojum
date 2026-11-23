# Issue #692: opt-in sqlite-vec Noosphere projection (experimental)

Status: isolated prototype, **not** canonical memory, a trained embedding model,
or an authenticated/encrypted distributed service. Source is still the single-owner
`LocalNoosphere` append-only SQLite journal; **no canonical schema or journal changes**.

## Dependency, provenance and threat boundary

- Opt-in Python package: `sqlite-vec==0.1.9` (`pip install 'sqlite-vec==0.1.9'`).
  Released Mar 31, 2026. Upstream: <https://github.com/asg017/sqlite-vec>.
  Dual licensed MIT / Apache-2.0. Pre-1.0: **no upgrades without test and review**.
- Core dependencies remain empty. Import does not load native extensions; only the
  explicit `rebuild_index` and `search_index` functions load the **installed
  Python package**, with an exact package/runtime version check. Arbitrary
  extension filenames are never accepted. Loading is turned off immediately.
  Native extension loading may be prohibited on some Python/SQLite builds;
  absence or failure must be reported, not silently bypassed.
- Caller supplies embeddings from an independently approved local model.
  This module does **not** download a model, invoke a service, generate embeddings,
  authorize content, access live organs, or create public projections.
- **Required independent SHA-256 head anchor**: `trusted_head` comes from a
  trusted source *outside* the journal; journal self-hash is not authentication.
  `verify_chain(strict_wire=True)` and event enumeration run in one read-only
  SQLite snapshot. Any source head change invalidates every query until rebuild.
- Scope: one owner, one `PUBLIC`/`PRIVATE`/`RESTRICTED` visibility, one model
  identity, embedding dimension, `valid_at_ns` and exact source head per file.
  Nonpublic indexing needs explicit `allow_nonpublic=True`, and on POSIX an
  owner-only parent directory (mode 0700). Derived files are mode 0600.
  **Embeddings of PRIVATE events are themselves sensitive**. Filesystem modes
  do not provide at-rest encryption; only use an encrypted owner-controlled
  volume for nonpublic use. Do not serve these files over HTTP or sync them to
  an untrusted host.
- Read/write admission rejects a symlink at the index file, its direct parent,
  **or any ancestor path component**. This is a preflight against existing
  aliases, not an atomic directory-descriptor traversal: a concurrent adversary
  able to rename directories may still cause TOCTOU. Use trusted, owner-controlled
  parent paths; do not treat this check as a secure filesystem sandbox.
- No original event payload, source path or source revision is stored in the
  derived index. Results return source event IDs/hashes and distances only,
  never unrestricted payloads. PRIVATE/RESTRICTED queries require explicit
  opt-in *again* and fail on any scope mismatch. Access control relies on the
  calling process and OS permissions, not on an in-DB password.

## Minimal local workflow

```python
from mojum.sqlite_vec_projection import rebuild_index, search_index

# You must obtain/verify this head using a separate trusted record.
trusted_head = "<64 lowercase hex digits from your independent anchor>"
source_path = "./private/organism.sqlite"        # existing canonical journal
index_path = "./private/derived-vectors.sqlite"  # different, derived file

# Explicit synthetic/example vector values; replace with a vetted local model.
summary = rebuild_index(
    source_path, index_path,
    owner="my-organism", trusted_head=trusted_head,
    visibility="PUBLIC", model="my-local-embedding-model/revision-1",
    dimension=2, vectors={"known-event-id": [0.2, 0.8]},
)
results = search_index(
    index_path, owner="my-organism", trusted_head=trusted_head,
    visibility="PUBLIC", model="my-local-embedding-model/revision-1",
    dimension=2, query=[0.2, 0.8], k=5,
)
```

On a change to the journal, a validity expiration/revocation, an embedding
model revision, or a corruption: derive a **complete new snapshot** with the
new independent anchor. Use `replace=True` only for an existing derived index
of the same owner/visibility/model/dimension, or choose a new index path for
corrupt files. A caller that omits a revoked event from the vector input causes
it to be removed in the rebuilt derived file. Never mutate old journals.

## Determinism and resource limits

- Input is bounded to 4,096 journal events / supplied vectors per rebuild.
  Vectors: 1 to 256 finite Float32 dimensions, magnitude <= 1,000,000. Numeric
  bool, NaN, infinities, wrong dimensions, surplus/unknown IDs fail closed.
- Rebuild orders event IDs deterministically; search compares all stored
  distances (max 4,096) and then sorts by `(distance, event_id)` to resolve
  exact ties. Query budget: 1 to 100 results. No inference or network cost.
- Theoretical raw Float32 vector bytes <= 4,194,304 (4096 * 256 * 4), not
  including SQLite virtual-table storage, index overhead, page cache or the
  temporary on-disk file during atomic replacement. Actual CPU/RAM/disk
  benchmarks depend on the optional native SQLite package/host; **not measured
  in the dependency-free CI environment**. Do not make production SLA claims.
- Existing index replacement is atomic per file, not a distributed transaction.
  Reader queries are isolated to that derived file. The caller must enforce
  single-writer authority and manage OS-level backups/revocation of old copies.

## Verification

Dependency-free fixture: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_sqlite_vec_projection.py' -v`.
The same tests add real vec0 create/rebuild/query/corruption/replace checks when
`sqlite-vec==0.1.9` is installed. For a full optional test, install the exact
pin in a disposable venv and rerun. PRIVATE content and production journals
must never be used as CI fixtures.

This adapter is not a Noosphere 3.0 release, a substitute for disk encryption,
or a Cezar scheduling/authorization boundary. Independent PR exact-head QA is
required before merging.
