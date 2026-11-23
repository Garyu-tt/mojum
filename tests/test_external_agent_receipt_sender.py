"""Regression for the isolated #717 offline receipt producer.

No network, real workers, user data or production signing keys are used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.external_agent_heartbeat import ReceiptError, ReceiptJournal
from tools.external_agent_receipt_sender import (
    SenderError,
    read_private_signing_key,
    signed_packet,
)

TS = 1_795_438_800_000
KEY = bytes(range(32))


def event(kind: str = "agent.started", *, seq: int = 1,
          status: str = "none") -> dict:
    return {
        "schema": "mojum.external-agent-receipt/1",
        "source": "cpu-legion", "agent_id": "worker-17", "host": "offline-host",
        "run_id": "run-abcdef", "session_id": "session-1",
        "repo": "Garyu-tt/mojum", "provider": "cpu-local",
        "kind": kind, "seq": seq, "timestamp_ms": TS + 1000 * (seq - 1),
        "ttl_ms": 30000, "issue": 717, "pr": None, "sha": None,
        "status_code": status,
    }


def packet(value: dict, key: bytes = KEY) -> dict:
    raw = json.dumps(value, separators=(",", ":")).encode("ascii")
    result = signed_packet(raw, key)
    if not result.endswith(b"\n") or result.count(b"\n") != 1:
        raise AssertionError("producer must emit exactly one line")
    return json.loads(result)


@unittest.skipUnless(os.name == "posix", "strict POSIX key-file contract")
class PrivateKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key_file = Path(self.tmp.name) / "hmac.key"
        self.key_file.write_bytes(KEY)
        self.key_file.chmod(0o600)

    def test_owner_only_binary_key(self):
        self.assertEqual(read_private_signing_key(self.key_file), KEY)
        self.key_file.chmod(0o400)
        self.assertEqual(read_private_signing_key(self.key_file), KEY)

    def test_readonly_relative_file_refused(self):
        with self.assertRaises(SenderError):
            read_private_signing_key("hmac.key")

    def test_world_readable_key_refused(self):
        self.key_file.chmod(0o644)
        with self.assertRaises(SenderError):
            read_private_signing_key(self.key_file)

    def test_owner_execute_bit_refused(self):
        self.key_file.chmod(0o700)
        with self.assertRaises(SenderError):
            read_private_signing_key(self.key_file)

    def test_symlink_refused(self):
        link = Path(self.tmp.name) / "link.key"
        link.symlink_to(self.key_file)
        with self.assertRaises(SenderError):
            read_private_signing_key(link)

    def test_hardlink_refused(self):
        link = Path(self.tmp.name) / "hardlink.key"
        os.link(self.key_file, link)
        with self.assertRaises(SenderError):
            read_private_signing_key(self.key_file)

    def test_symlinked_ancestor_directory_refused(self):
        # O_NOFOLLOW on the leaf alone would still open this key.
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.key_file.parent, target_is_directory=True)
        with self.assertRaises(SenderError):
            read_private_signing_key(alias / self.key_file.name)

    def test_key_path_parent_traversal_refused(self):
        intermediate = Path(self.tmp.name) / "intermediate"
        intermediate.mkdir()
        with self.assertRaises(SenderError):
            read_private_signing_key(intermediate / ".." / self.key_file.name)

    def test_short_and_oversized_key_refused(self):
        for blob in (b"a" * 31, b"x" * 4097):
            self.key_file.write_bytes(blob)
            with self.assertRaises(SenderError):
                read_private_signing_key(self.key_file)

    def test_cli_never_echoes_key_on_failure_or_success(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "external_agent_receipt_sender.py"
        cmd = [sys.executable, str(script), "--key-file", str(self.key_file)]
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        ok = subprocess.run(cmd, input=json.dumps(event()).encode(),
                            capture_output=True, env=env, check=False)
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        self.assertEqual(json.loads(ok.stdout)["event"]["run_id"], "run-abcdef")
        self.assertNotIn(KEY, ok.stdout)
        self.assertEqual(ok.stderr, b"")
        self.key_file.chmod(0o644)
        bad = subprocess.run(cmd, input=json.dumps(event()).encode(),
                             capture_output=True, env=env, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, b"")
        self.assertNotIn(KEY, bad.stderr)
        self.assertNotIn(str(self.key_file).encode(), bad.stderr)


class SignedPacketTests(unittest.TestCase):
    def test_roundtrip_into_existing_sqlite_receipt_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "receipts.sqlite")
            trust = {("cpu-legion", "worker-17", "Garyu-tt/mojum"): KEY}
            with ReceiptJournal(db_path, trust) as ledger:
                started = packet(event())
                self.assertTrue(ledger.ingest(started["event"], started["signature"],
                                              observed_ms=TS))
                self.assertFalse(ledger.ingest(started["event"], started["signature"],
                                               observed_ms=TS))
                self.assertEqual(ledger.status("cpu-legion", "worker-17", "run-abcdef",
                                               observed_ms=TS)["state"], "active")
                beat = packet(event("agent.heartbeat", seq=2))
                self.assertTrue(ledger.ingest(beat["event"], beat["signature"],
                                              observed_ms=TS + 1000))
                self.assertEqual(ledger.status("cpu-legion", "worker-17", "run-abcdef",
                                               observed_ms=TS + 31_000)["state"], "stale")
                done = packet(event("agent.finished", seq=3, status="completed"))
                self.assertTrue(ledger.ingest(done["event"], done["signature"],
                                              observed_ms=TS + 2000))
                self.assertEqual(ledger.status("cpu-legion", "worker-17", "run-abcdef",
                                               observed_ms=TS + 100_000)["state"], "finished")

    def test_tampering_after_signature_rejected_by_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            trust = {("cpu-legion", "worker-17", "Garyu-tt/mojum"): KEY}
            with ReceiptJournal(str(Path(tmp) / "db.sqlite"), trust) as ledger:
                signed = packet(event())
                signed["event"]["host"] = "impersonator"
                with self.assertRaises(ReceiptError):
                    ledger.ingest(signed["event"], signed["signature"], observed_ms=TS)

    def test_unknown_field_refused(self):
        value = event()
        value["private_notes"] = "not for signing"
        with self.assertRaises(SenderError):
            packet(value)

    def test_extra_json_and_duplicate_field_refused(self):
        good = json.dumps(event()).encode()
        duplicate = good.replace(b'"seq": 1', b'"seq": 1, "seq": 2')
        self.assertNotEqual(duplicate, good)
        for raw in (good + b" {}", duplicate):
            with self.assertRaises(SenderError):
                signed_packet(raw, KEY)

    def test_nonfinite_and_non_utf8_refused(self):
        raw = json.dumps(event()).replace('"ttl_ms": 30000', '"ttl_ms": NaN').encode()
        with self.assertRaises(SenderError):
            signed_packet(raw, KEY)
        with self.assertRaises(SenderError):
            signed_packet(b"\xff", KEY)

    def test_bounded_input_and_invalid_identity_refused(self):
        for raw in (b"", b" " * 2049, b"[]"):
            with self.assertRaises(SenderError):
                signed_packet(raw, KEY)
        invalid = event()
        invalid["agent_id"] = "worker/../../escape"
        with self.assertRaises(SenderError):
            packet(invalid)

    def test_weak_key_refused_without_signed_output(self):
        with self.assertRaises(SenderError):
            packet(event(), b"short")

    def test_distinct_kind_is_not_inferred_from_schedule(self):
        # This producer signs exact, explicit events only.
        schedule = {"schedule": "hourly", "repo": "Garyu-tt/mojum"}
        with self.assertRaises(SenderError):
            packet(schedule)


if __name__ == "__main__":
    unittest.main()
