"""Run the full stdlib test suite and catch SQLite GC resource leaks.

Intended for CI, especially Python 3.13: a passing unittest suite must not
silently hide ResourceWarning("unclosed database") from a deferred destructor.
No private journals; ordinary disposable synthetic test fixtures only.
"""
from __future__ import annotations

import gc
import sys
import unittest
import warnings


def main() -> int:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always", ResourceWarning)
        suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
        outcome = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
        # sqlite3.Connection warnings can appear during delayed GC, long after
        # the test that allocated the leaked handle has already finished.
        collected = gc.collect()
    sqlite_leaks = [
        w for w in emitted
        if issubclass(w.category, ResourceWarning)
        and "unclosed database" in str(w.message)
    ]
    print(
        "SQLITE_LEAK_AUDIT tests=%d gc_collected=%d unclosed_databases=%d"
        % (outcome.testsRun, collected, len(sqlite_leaks)),
        flush=True,
    )
    for leaked in sqlite_leaks:
        print("SQLITE_LEAK_AUDIT_FAIL %s:%s %s" %
              (leaked.filename, leaked.lineno, leaked.message), file=sys.stderr)
    return int(not outcome.wasSuccessful() or bool(sqlite_leaks))


if __name__ == "__main__":
    sys.exit(main())
