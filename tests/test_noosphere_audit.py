"""Stdlib synthetic regression tests for the read-only Noosphere auditor (#929)."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere
from mojum.noosphere_audit import (
    NoosphereAuditError, REPORT_SCHEMA, audit_noosphere, main,
)


def synthetic_event(event_id="private-1", *, visibility="PRIVATE"):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": event_id,
        "agent_id": "synthetic-dante",
        "topic": "memory",
        "observed_at_ns": 10,
        "recorded_at_ns": 20,
        "valid_from_ns": 10,
        "valid_to_ns": None,
        "visibility": visibility,
        "source": "synthetic:offline",
        "source_revision": "fixture/1",
        "payload": {"secret": "NEVER_PRINT_PRIVATE_PAYLOAD"},
    }


class NoosphereAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "PRIVATE-noosphere.sqlite3"
        with LocalNoosphere(self.db, "synthetic-dante") as store:
            store.append(synthetic_event(), expected_head=store.head())
            self.head = store.head()

    def _run_cli(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            status = main(["--db", str(self.db), "--agent-id", "synthetic-dante", *args])
        return status, output.getvalue(), json.loads(output.getvalue())

    def test_local_chain_is_audited_without_exposing_memory(self):
        result = audit_noosphere(self.db, "synthetic-dante")
        self.assertEqual(result["schema"], REPORT_SCHEMA)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["head"], self.head)
        self.assertEqual(result["trust"], "LOCAL_CHAIN_ONLY_NOT_AUTHENTICATED")
        self.assertFalse(result["expected_head_verified"])
        self.assertNotIn("NEVER_PRINT", json.dumps(result))
        code, raw, output = self._run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(output, result)
        self.assertNotIn("NEVER_PRINT_PRIVATE_PAYLOAD", raw)
        self.assertNotIn("synthetic-dante", raw)
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(raw.count("\n"), 1)

    def test_matching_independent_head_is_explicitly_attested(self):
        result = audit_noosphere(self.db, "synthetic-dante", expected_head=self.head)
        self.assertTrue(result["expected_head_verified"])
        self.assertEqual(result["trust"], "EXTERNAL_HEAD_MATCHED_NOT_A_SIGNATURE")
        code, _, output = self._run_cli("--expected-head", self.head)
        self.assertEqual(code, 0)
        self.assertEqual(output, result)

    def test_wrong_independent_head_fails_closed(self):
        wrong = ("f" if self.head[0] != "f" else "e") + self.head[1:]
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(self.db, "synthetic-dante", expected_head=wrong)
        code, raw, output = self._run_cli("--expected-head", wrong)
        self.assertEqual(code, 1)
        self.assertEqual(output["status"], "FAIL")
        self.assertNotIn("NEVER_PRINT", raw)
        self.assertNotIn(str(self.db), raw)

    def test_invalid_head_rejected_before_db_open(self):
        for head in ("0" * 63, "G" * 64, "A" * 64, True, 10, ""):
            with self.subTest(head=head):
                with patch("mojum.noosphere_audit.LocalNoosphere",
                           side_effect=AssertionError("must not open")):
                    with self.assertRaises(NoosphereAuditError):
                        audit_noosphere(self.db, "synthetic-dante", expected_head=head)

    def test_unknown_owner_is_not_leaked(self):
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(self.db, "other-agent")
        code, raw, _ = self._run_cli("--expected-head", "0" * 64)
        self.assertEqual(code, 1)
        self.assertNotIn("NEVER_PRINT", raw)

    def test_missing_file_is_not_created(self):
        absent = Path(self.temp.name) / "not-here.sqlite3"
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(absent, "synthetic-dante")
        self.assertFalse(absent.exists())

    def test_file_symlink_and_symlinked_parent_rejected(self):
        direct = Path(self.temp.name) / "symlink.sqlite3"
        parent = Path(self.temp.name) / "symlink-parent"
        try:
            direct.symlink_to(self.db)
            parent.symlink_to(Path(self.temp.name), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        for path in (direct, parent / self.db.name):
            with self.subTest(path=path):
                with self.assertRaises(NoosphereAuditError):
                    audit_noosphere(path, "synthetic-dante")

    def test_parent_traversal_rejected(self):
        path = str(self.db.parent) + "/fake/../" + self.db.name
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(path, "synthetic-dante")

    def test_noncanonical_wire_fails_even_when_hash_matches(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            raw = db.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()[0]
            # Same object/hash, different raw wire: strict_wire must catch it.
            db.execute("UPDATE events SET wire_json=? WHERE seq=1", (" " + raw,))
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(self.db, "synthetic-dante")

    def test_broken_index_cannot_be_claimed_clean(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET visibility='PUBLIC' WHERE seq=1")
        with self.assertRaises(NoosphereAuditError):
            audit_noosphere(self.db, "synthetic-dante")

    def test_giant_foreign_wire_is_rejected_before_json_parsing(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET wire_json=? WHERE seq=1", ("z" * 128001,))
        with patch("mojum.noosphere._loads_wire",
                   side_effect=AssertionError("oversized wire must not decode")):
            with self.assertRaises(NoosphereAuditError):
                audit_noosphere(self.db, "synthetic-dante")

    def test_no_mutation_of_database_bytes(self):
        before = self.db.read_bytes()
        first = audit_noosphere(self.db, "synthetic-dante", expected_head=self.head)
        second = audit_noosphere(self.db, "synthetic-dante", expected_head=self.head)
        self.assertEqual(first, second)
        self.assertEqual(self.db.read_bytes(), before)

    def test_empty_valid_journal_is_admissible(self):
        empty = Path(self.temp.name) / "empty.sqlite3"
        with LocalNoosphere(empty, "synthetic-dante"):
            pass
        report = audit_noosphere(empty, "synthetic-dante")
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["head"], "0" * 64)
        self.assertEqual(report["status"], "PASS")

    def test_failure_output_is_fixed_and_payload_free(self):
        with sqlite3.connect(self.db) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET wire_json=? WHERE seq=1",
                       ("PRIVATE_SECRET_NEVER_PRINT",))
        code, raw, output = self._run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(output, {"schema": REPORT_SCHEMA, "status": "FAIL",
                                  "error": "journal_verification_failed"})
        self.assertNotIn("PRIVATE_SECRET", raw)
        self.assertNotIn(str(self.db), raw)


if __name__ == "__main__":
    unittest.main()
