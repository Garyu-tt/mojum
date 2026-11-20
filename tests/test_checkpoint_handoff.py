"""Offline-only checkpoint transport regression: no network, no live organism."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.checkpoint_bundle import MAX_COMPONENT_BYTES
from mojum.checkpoint_handoff import (
    FILE_NAMES, export_new_archive, read_verified_archive, restore_new_directory,
)


ROOT = Path(__file__).resolve().parent.parent
NAMES = ("genome", "neural", "body", "world", "memory")


class OfflineCheckpointHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parts = {
            "genome": b'{"seed":"synthetic"}',
            "neural": b"synthetic-weight-state",
            "body": b"body-fixture",
            "world": b"world-fixture",
            "memory": b"PRIVATE:synthetic-memory",
        }
        self.files = {}
        for name in NAMES:
            path = self.root / (name + ".input")
            path.write_bytes(self.parts[name])
            self.files[name] = path
        self.archive = self.root / "transport.mojum.json"

    def export(self, organism_id="dante-fixture-001", revision=17):
        return export_new_archive(organism_id, revision, self.files, self.archive)

    def test_cross_process_cli_export_verify_and_restore(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        prefix = [sys.executable, "-m", "mojum.checkpoint_handoff"]
        output = [*prefix, "export", "--organism-id", "dante-fixture-001",
                  "--revision", "17", "--output", str(self.archive)]
        for name in NAMES:
            output += ["--" + name, str(self.files[name])]
        export = json.loads(subprocess.check_output(output, env=env, text=True))
        self.assertEqual(export["revision"], 17)
        self.assertEqual(export["status"], "VERIFIED_FIXTURE_COMPONENTS_NOT_LIVE_RESTORE")
        self.assertNotIn("PRIVATE:synthetic-memory", json.dumps(export))
        verify = [*prefix, "verify", "--archive", str(self.archive),
                  "--organism-id", "dante-fixture-001", "--revision", "17"]
        self.assertEqual(
            json.loads(subprocess.check_output(verify, env=env, text=True))["manifest_sha256"],
            export["manifest_sha256"],
        )
        restored_dir = self.root / "imported"
        restore = [*prefix, "restore", "--archive", str(self.archive),
                   "--organism-id", "dante-fixture-001", "--revision", "17",
                   "--directory", str(restored_dir)]
        saved = json.loads(subprocess.check_output(restore, env=env, text=True))
        self.assertEqual(saved["manifest_sha256"], export["manifest_sha256"])
        for name in NAMES:
            self.assertEqual((restored_dir / FILE_NAMES[name]).read_bytes(), self.parts[name])
        self.assertEqual(json.loads((restored_dir / "manifest.json").read_text())["revision"], 17)

    def test_deterministic_wire_and_hashes(self):
        manifest = self.export()
        another = self.root / "another.json"
        self.assertEqual(
            export_new_archive("dante-fixture-001", 17, dict(reversed(list(self.files.items()))), another),
            manifest,
        )
        self.assertEqual(self.archive.read_bytes(), another.read_bytes())

    def test_reject_other_organism_even_when_revision_matches(self):
        self.export(organism_id="dante-b")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            read_verified_archive(self.archive, expected_organism_id="dante-a", expected_revision=17)
        dest = self.root / "should-not-exist"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            restore_new_directory(self.archive, dest, expected_organism_id="dante-a")
        self.assertFalse(dest.exists())

    def test_reject_stale_revision_before_creation(self):
        self.export()
        dest = self.root / "wrong-revision"
        with self.assertRaisesRegex(ValueError, "stale"):
            restore_new_directory(
                self.archive, dest, expected_organism_id="dante-fixture-001",
                expected_revision=16,
            )
        self.assertFalse(dest.exists())

    def test_reject_missing_expected_identity(self):
        self.export()
        for invalid in (None, False, "", 7):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                read_verified_archive(self.archive, expected_organism_id=invalid)

    def test_reject_modified_wire_before_directory_creation(self):
        self.export()
        raw = self.archive.read_bytes()
        self.archive.write_bytes(raw.replace(b'"revision":17', b'"revision":18', 1))
        dest = self.root / "untrusted"
        with self.assertRaises(ValueError):
            restore_new_directory(self.archive, dest, expected_organism_id="dante-fixture-001")
        self.assertFalse(dest.exists())

    def test_existing_archive_and_directory_never_replaced(self):
        self.archive.write_bytes(b"existing archive")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export()
        self.assertEqual(self.archive.read_bytes(), b"existing archive")
        self.archive.unlink()
        self.export()
        dest = self.root / "already-here"
        dest.mkdir()
        (dest / "marker").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            restore_new_directory(self.archive, dest, expected_organism_id="dante-fixture-001")
        self.assertEqual((dest / "marker").read_bytes(), b"keep")

    def test_oversized_component_rejected_before_archive_created(self):
        self.files["genome"].write_bytes(b"x" * (MAX_COMPONENT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "size limit"):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_input_symlink_rejected(self):
        origin = self.files["memory"]
        link = self.root / "memory-link"
        try:
            link.symlink_to(origin)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks unavailable")
        self.files["memory"] = link
        with self.assertRaisesRegex(ValueError, "symbolic-link"):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_restore_failure_cleans_stage_and_does_not_publish(self):
        self.export()
        dest = self.root / "not-installed"
        with patch("mojum.checkpoint_handoff._write_private", side_effect=OSError("simulated disk fault")):
            with self.assertRaises(OSError):
                restore_new_directory(self.archive, dest, expected_organism_id="dante-fixture-001")
        self.assertFalse(dest.exists())
        self.assertEqual(list(self.root.glob(".mojum-restore-*")), [])

    def test_publish_race_never_overwrites_archive(self):
        with patch("mojum.checkpoint_handoff.os.link", side_effect=FileExistsError("race")):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.export()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob(".mojum-export-*")), [])

    @unittest.skipUnless(os.name == "posix", "POSIX file mode assertions")
    def test_new_component_permissions_are_private(self):
        self.export()
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        dest = self.root / "private-restore"
        restore_new_directory(self.archive, dest, expected_organism_id="dante-fixture-001")
        self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
        for file in dest.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
