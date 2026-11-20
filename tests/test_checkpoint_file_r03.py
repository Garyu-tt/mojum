"""Offline checkpoint file transport: no-clobber, bounded, identity-pinned."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.checkpoint_bundle import dumps, pack
from mojum.checkpoint_file import (
    MAX_WIRE_BYTES, _TEMP_PREFIX,
    export_checkpoint_file, import_checkpoint_file,
)

PARTS = {name: name.encode("ascii") for name in (
    "genome", "neural", "body", "world", "memory",
)}


class OfflineCheckpointFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "dante-export.mojum.json"
        self.bundle = pack("dante", 7, PARTS)

    def test_canonical_private_file_roundtrip(self):
        export_checkpoint_file(self.path, self.bundle)
        self.assertEqual(self.path.read_text(encoding="ascii"), dumps(self.bundle))
        manifest, parts = import_checkpoint_file(
            self.path, expected_organism_id="dante", expected_revision=7)
        self.assertEqual(manifest["organism_id"], "dante")
        self.assertEqual(manifest["revision"], 7)
        self.assertEqual(parts, PARTS)
        self.assertFalse(list(self.root.glob(_TEMP_PREFIX + "*")))
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_export_cannot_clobber_existing_file(self):
        export_checkpoint_file(self.path, self.bundle)
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            export_checkpoint_file(self.path, pack("dante", 8, PARTS))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(_TEMP_PREFIX + "*")))

    def test_restoring_another_organism_or_revision_fails_closed(self):
        export_checkpoint_file(self.path, self.bundle)
        for organism, revision in (("stranger", 7), ("dante", 6), ("stranger", 8)):
            with self.subTest(organism=organism, revision=revision):
                with self.assertRaises(ValueError):
                    import_checkpoint_file(self.path,
                        expected_organism_id=organism, expected_revision=revision)

    def test_expected_identity_and_revision_are_mandatory(self):
        for identity, revision in ((None, 7), (False, 7), ([], 7), ("dante", True),
                                   ("dante", -1), ("", 7)):
            with self.subTest(identity=identity, revision=revision):
                with self.assertRaises(ValueError):
                    import_checkpoint_file(self.path,
                        expected_organism_id=identity, expected_revision=revision)

    def test_corrupt_and_noncanonical_file_never_returns_components(self):
        for modifier in (lambda x: x + b" ",
                         lambda x: x.replace(b'"schema":', b'"schema":"fake","schema":', 1),
                         lambda x: x[:-1],
                         lambda x: b"\xff" + x):
            with self.subTest(modifier=modifier):
                self.path.write_bytes(modifier(dumps(self.bundle).encode("ascii")))
                with self.assertRaises(ValueError):
                    import_checkpoint_file(self.path,
                        expected_organism_id="dante", expected_revision=7)

    def test_sparse_oversized_file_is_rejected_before_read(self):
        with self.path.open("wb") as out:
            out.seek(MAX_WIRE_BYTES)
            out.write(b"x")
        with self.assertRaisesRegex(ValueError, "oversized"):
            import_checkpoint_file(self.path,
                expected_organism_id="dante", expected_revision=7)

    def test_failed_publication_leaves_no_destination_or_tempfile(self):
        with patch("mojum.checkpoint_file.os.link", side_effect=OSError("unsupported")):
            with self.assertRaises(OSError):
                export_checkpoint_file(self.path, self.bundle)
        self.assertFalse(self.path.exists())
        self.assertFalse(list(self.root.glob(_TEMP_PREFIX + "*")))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_existing_symlink_is_not_overwritten(self):
        protected = self.root / "protected"
        protected.write_bytes(b"do-not-overwrite")
        try:
            self.path.symlink_to(protected)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        with self.assertRaises(FileExistsError):
            export_checkpoint_file(self.path, self.bundle)
        self.assertEqual(protected.read_bytes(), b"do-not-overwrite")
        if hasattr(os, "O_NOFOLLOW"):
            with self.assertRaises(OSError):
                import_checkpoint_file(self.path,
                    expected_organism_id="dante", expected_revision=7)

    def test_directory_cannot_be_read_as_a_checkpoint(self):
        with self.assertRaises((ValueError, OSError)):
            import_checkpoint_file(self.root,
                expected_organism_id="dante", expected_revision=7)


if __name__ == "__main__":
    unittest.main()
