# SQLite schema fingerprint R0.1 QA notes

`mojum.sqlite_schema_fingerprint.fingerprint(path)` is an offline, metadata-only diagnostic. It hashes ordered `sqlite_schema` metadata and does not read application rows or emit schema names/SQL.

## Resource lifecycle

The SQLite connection is explicitly closed with `contextlib.closing` on success and on exceptions. Its transaction context is nested inside the closing context; `Connection.__exit__` alone does not close the handle. Regression coverage repeatedly invokes the function with `ResourceWarning` elevated.

## Filesystem trust boundary

The initial `lstat()` rejects a path that is already a symlink and enforces the main database file's initial size. It is **not** an fd-bound no-follow open: callers must use a trusted directory that is not concurrently modified. This utility does not claim resistance to rename/symlink TOCTOU races. SQLite may consult WAL sidecars; the main-file size check is not a bound on sidecar sizes or total I/O. Do not use this utility as a hostile-path sandbox or as a content-integrity/backup validator.

## Verification

Focused tests: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_sqlite_schema_fingerprint.py' -v`. Exact-head CPython 3.11/3.12/3.13 CI is provided by the companion workflow.
