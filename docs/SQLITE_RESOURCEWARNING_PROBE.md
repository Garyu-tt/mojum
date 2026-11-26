# SQLite ResourceWarning diagnostic (issue #121)

Scope: standalone opt-in probe for the Python 3.13 unclosed sqlite3.Connection warning. This is **not** a verified root-cause fix.

Run from the repository root:

```sh
PYTHONPATH=src python3.13 tools/sqlite_resourcewarning_probe.py tests.test_aquarium
```

The runner accepts *unittest target names*, not unittest CLI flags. For a whole-suite discovery run use `python3.13 -X tracemalloc=25 -W error::ResourceWarning -m unittest discover -s tests` directly.

For a focused case:

```sh
PYTHONPATH=src python3.13 tools/sqlite_resourcewarning_probe.py tests.test_aquarium
```

The runner itself does not write project files. The selected tests execute in a child process with the caller's normal filesystem permissions and retain their ordinary side effects; this is process separation, not a filesystem sandbox. The child interpreter runs with `-X tracemalloc=25` and `ResourceWarning` promoted to an error. This can expose warnings emitted synchronously, but **unraisable exceptions in destructors may not change the process exit code**. Always inspect stderr for `Exception ignored in:`, `ResourceWarning`, and allocation traceback. Use `PYTHONTRACEMALLOC=1` and `gc.collect()` near suspect test teardown to localize deferred finalization. Fix the owning connection with explicit `close()` in `finally` or an appropriate closing context manager; `with sqlite3.connect(...)` alone commits/rolls back but does **not** close the connection.

Acceptance remains open until full 3.11/3.12/3.13 CI and actual 3.13 warning-free logs are observed. No suppression or production journal mutation.
