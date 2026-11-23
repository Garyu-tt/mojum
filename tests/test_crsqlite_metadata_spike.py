"""Offline CPU-only tests; no native extension or Noosphere required."""
import base64
import hashlib
from itertools import count
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

from mojum import crsqlite_metadata_spike as cr


class MockRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def change(value="ready", site=b"0" * 16):
    return ("collab_notes", b"\x01\x03foo", "value", value, 1, 1, site, 1, 0)


class EnvelopeTests(unittest.TestCase):
    def test_round_trip_and_explicit_schema(self):
        expected = change()
        blob = cr.pack_changes([expected])
        self.assertEqual(cr.unpack_changes(blob), (expected,))
        self.assertEqual(json.loads(blob)["schema"], cr.SCHEMA)

    def test_empty_changes_are_valid(self):
        self.assertEqual(cr.unpack_changes(cr.pack_changes([])), ())

    def test_reject_unapproved_table_or_unsafe_content(self):
        for field in ("home/username", "secret\nvalue", "🔥", "", "x" * 65):
            with self.subTest(field=field), self.assertRaises(ValueError):
                cr.pack_changes([change(value=field)])
        with self.assertRaises(ValueError):
            cr.pack_changes([("organism_journal", *change()[1:])])
        with self.assertRaises(ValueError):
            cr.pack_changes([("collab_notes", change()[1], "other", *change()[3:])])

    def test_reject_invalid_binary_lengths_and_numeric_types(self):
        for site in (b"", b"short", b"x" * 17):
            with self.subTest(site=site), self.assertRaises(ValueError):
                cr.pack_changes([change(site=site)])
        for counter in (-1, True, None, 2**53):
            with self.subTest(counter=counter), self.assertRaises(ValueError):
                row = list(change())
                row[5] = counter
                cr.pack_changes([row])

    def test_reject_duplicate_keys_and_unapproved_fields(self):
        data = cr.pack_changes([change()]).decode()
        self.assertRaises(ValueError, cr.unpack_changes, data.replace('"schema":', '"schema":"oops","schema":', 1).encode())
        parsed = json.loads(data)
        parsed["PRIVATE"] = "not allowed"
        with self.assertRaises(ValueError):
            cr.unpack_changes(json.dumps(parsed).encode())
        parsed.pop("PRIVATE")
        parsed["changes"][0]["host_id"] = "machine"
        with self.assertRaises(ValueError):
            cr.unpack_changes(json.dumps(parsed).encode())

    def test_reject_invalid_base64_and_schema(self):
        data = json.loads(cr.pack_changes([change()]))
        data["changes"][0]["site_id"] = "nonbase64%"
        with self.assertRaises(ValueError):
            cr.unpack_changes(json.dumps(data).encode())
        data["changes"][0]["site_id"] = base64.b64encode(b"x" * 16).decode()
        data["schema"] = "mojum.private-journal/1"
        with self.assertRaises(ValueError):
            cr.unpack_changes(json.dumps(data).encode())

    def test_bounded_packets_and_arrays(self):
        with self.assertRaises(ValueError):
            cr.pack_changes([change()] * (cr.MAX_CHANGES + 1))
        # Must stop after the first excess row rather than exhaust a stream.
        with self.assertRaises(ValueError):
            cr.pack_changes((change() for _ in count()))
        with self.assertRaises(ValueError):
            cr.unpack_changes(b"x" * (cr.MAX_WIRE + 1))
        with self.assertRaises(ValueError):
            cr.unpack_changes(b"[" * 15000 + b"]" * 15000)

    def test_corrupted_and_wrong_type_rejected(self):
        for blob in ("string", bytearray(b"{}"), b"", b"{}", b"{bad"):
            with self.subTest(blob=repr(blob)), self.assertRaises(ValueError):
                cr.unpack_changes(blob)


class BoundaryTests(unittest.TestCase):
    def test_validate_batch_before_any_sql_mutation(self):
        conn = MagicMock()
        with self.assertRaises(ValueError):
            cr.apply_changes(conn, b"{}")
        conn.execute.assert_not_called()

    def test_sql_statements_are_fixed_and_parameterized(self):
        conn = MagicMock()
        conn.execute.side_effect = lambda sql, args=None: (
            MockRows([(0, "main", "")]) if sql == "PRAGMA database_list"
            else MockRows([change()]) if sql == cr._SELECT
            else MockRows([])
        )
        encoded = cr.export_changes(conn)
        self.assertEqual(cr.unpack_changes(encoded), (change(),))
        self.assertEqual(conn.execute.call_args_list[-1].args,
                         (cr._SELECT, (cr.MAX_CHANGES + 1,)))
        conn.reset_mock()
        cr.apply_changes(conn, encoded)
        self.assertEqual(conn.execute.call_args_list[-1].args, (cr._INSERT, change()))
        self.assertNotIn("PRIVATE", cr._INSERT)

    def test_export_fails_on_truncation(self):
        conn = MagicMock()
        conn.execute.side_effect = lambda sql, args=None: (
            MockRows([(0, "main", "")]) if sql == "PRAGMA database_list"
            else MockRows([change()] * (cr.MAX_CHANGES + 1))
        )
        with self.assertRaises(ValueError):
            cr.export_changes(conn)

    def test_real_plain_sqlite_must_not_be_mistaken_for_extension(self):
        with sqlite3.connect(":memory:") as conn:
            with self.assertRaises(sqlite3.OperationalError):
                cr.export_changes(conn)

    def test_filedb_is_never_used_even_with_crr_schema(self):
        with tempfile.TemporaryDirectory() as root:
            with sqlite3.connect(str(Path(root) / "private.db")) as conn:
                with self.assertRaisesRegex(ValueError, "in-memory"):
                    cr.export_changes(conn)
                with self.assertRaisesRegex(ValueError, "in-memory"):
                    cr.apply_changes(conn, cr.pack_changes([change()]))

    def test_native_file_must_be_explicitly_pinned(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "extension.so"
            p.write_bytes(b"test-only-not-a-real-extension")
            expected = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(cr.checked_extension(p, expected), p)
            with self.assertRaises(ValueError):
                cr.checked_extension(p, "0" * 64)
            with self.assertRaises(ValueError):
                cr.checked_extension(p, expected.upper())
            symlink = Path(root) / "other.so"
            symlink.symlink_to(p)
            with self.assertRaises(ValueError):
                cr.checked_extension(symlink, expected)
            with self.assertRaises(ValueError):
                cr.checked_extension(Path("extension.so"), expected)


if __name__ == "__main__":
    unittest.main()
