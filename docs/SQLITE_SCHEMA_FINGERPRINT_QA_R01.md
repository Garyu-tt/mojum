# SQLite schema fingerprint R0.1 QA notes

`mojum.sqlite_schema_fingerprint.fingerprint(path)` is an offline, metadata-only diagnostic. It hashes ordered `sqlite_schema` metadata and does not read application rows or emit schema names/SQL.

## Resource lifecycle

The SQLite connection is explicitly closed with `contextlib.closing` on success and on exceptions. Its transaction context is nested inside the closing context; `Connection.__exit__` alone does not close the handle. Regression coverage repeatedly invokes the function with `ResourceWarning` elevated.

Each metadata text field is rejected by character count before UTF-8 encoding if it exceeds 65,536 characters; aggregate UTF-8 metadata is capped at 4 MiB. The schema query has a hard SQLite virtual-machine instruction cutoff: the progress handler interrupts execution at the first callback after 100,000 VM instructions. This is a deterministic work cutoff, **not** a wall-time or memory limit. A valid but unusually complex schema may therefore be rejected with `SchemaAuditError`; the cutoff is intentional fail-closed behavior, not a claim that every schema under the entry/text limits will be accepted.

## Filesystem trust boundary

The initial `lstat()` rejects a path that is already a symlink and enforces the main database file's initial size. It is **not** an fd-bound no-follow open: callers must use a trusted directory that is not concurrently modified. This utility does not claim resistance to rename/symlink TOCTOU races. SQLite may consult WAL sidecars; the main-file size check is not a bound on sidecar sizes or total I/O. Do not use this utility as a hostile-path sandbox or as a content-integrity/backup validator.

Use a stable database snapshot. When the database header specifies WAL and
no WAL, SHM or rollback-journal file exists, the audit opens the snapshot with
SQLite's immutable option to avoid creating sidecars. Databases with existing
sidecars use the regular read-only connection so committed schema changes in
the WAL are included. [SQLite WAL documentation](https://sqlite.org/wal.html#readonly)

## Verification

Focused tests: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_sqlite_schema_fingerprint.py' -v`. Exact-head CPython 3.11/3.12/3.13 CI is provided by the companion workflow.
