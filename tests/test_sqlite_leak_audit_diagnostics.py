"""Keep SQLite allocation-site diagnostics useful without leaking test DBs."""
import runpy
import sqlite3
import tempfile
import tracemalloc
import unittest
import warnings
from pathlib import Path


_audit = runpy.run_path(str(Path(__file__).with_name("audit_sqlite_resource_warnings.py")))


class SQLiteLeakDiagnosticTests(unittest.TestCase):
    def test_warning_includes_connection_creation_site_with_tracemalloc(self):
        already_tracing = tracemalloc.is_tracing()
        if not already_tracing:
            tracemalloc.start(5)
        try:
            with tempfile.TemporaryDirectory() as directory:
                conn = sqlite3.connect(str(Path(directory) / "synthetic.sqlite3"))
                try:
                    leaked = warnings.WarningMessage(
                        ResourceWarning("unclosed database in test connection"),
                        ResourceWarning, "later_gc.py", 99, source=conn,
                    )
                    message = _audit["_leak_diagnostic"](leaked)
                    self.assertIn("later_gc.py:99", message)
                    self.assertIn("SQLite connection allocation (oldest first)", message)
                    self.assertIn("test_sqlite_leak_audit_diagnostics.py:", message)
                finally:
                    conn.close()
        finally:
            if not already_tracing:
                tracemalloc.stop()

    def test_missing_warning_source_has_actionable_fallback(self):
        leaked = warnings.WarningMessage(
            ResourceWarning("unclosed database"), ResourceWarning,
            "late_finalizer.py", 12,
        )
        message = _audit["_leak_diagnostic"](leaked)
        self.assertIn("late_finalizer.py:12", message)
        self.assertIn("rerun with PYTHONTRACEMALLOC=1", message)


if __name__ == "__main__":
    unittest.main()
