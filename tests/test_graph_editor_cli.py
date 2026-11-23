"""No-network, no-clobber CLI contract for issue #699."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from mojum.graph_editor_cli import main
from mojum.graph_v2 import upgrade_v1

ROOT = Path(__file__).resolve().parents[1]


class GraphEditorCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = json.loads((ROOT / "examples" / "first_loop.json")
                             .read_text(encoding="utf-8"))
        self.original = upgrade_v1(fixture)
        self.src = self.root / "graph.private.json"
        self.src.write_text(json.dumps(self.original), encoding="utf-8")
        self.editor = self.root / "editor.private.json"
        self.candidate = self.root / "candidate.private.json"

    def _run(self, args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(args), 0)
        return json.loads(stdout.getvalue())

    def _export(self, backend="reactflow"):
        return self._run(["export", "--input", str(self.src),
                          "--backend", backend, "--output", str(self.editor),
                          "--allow-private"])

    def test_round_trip_both_backends_and_no_live_mutation(self):
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                if self.editor.exists():
                    self.editor.unlink()
                if self.candidate.exists():
                    self.candidate.unlink()
                before = self.src.read_bytes()
                report = self._export(backend)
                self.assertEqual(report["nodes"], 6)
                self.assertEqual(report["execution"], "NOT_PERFORMED")
                envelope = json.loads(self.editor.read_text(encoding="utf-8"))
                self.assertEqual(envelope["backend"], backend)
                result = self._run([
                    "verify", "--input", str(self.editor),
                    "--expected-revision", report["base_revision"],
                    "--expected-sha256", report["base_sha256"],
                    "--output", str(self.candidate), "--allow-private",
                ])
                self.assertEqual(result["execution"], "NOT_PERFORMED")
                wrapped = json.loads(self.candidate.read_text(encoding="utf-8"))
                self.assertEqual(wrapped["graph"], self.original)
                self.assertEqual(wrapped["schema"], "mojum.graph-editor-candidate/1")
                self.assertEqual(self.src.read_bytes(), before)
                if os.name == "posix":
                    self.assertEqual(self.editor.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(self.candidate.stat().st_mode & 0o777,
                                     0o600)

    def test_private_export_denied_and_creates_no_target(self):
        with self.assertRaises(SystemExit) as err:
            main(["export", "--input", str(self.src), "--backend", "rete",
                  "--output", str(self.editor)])
        self.assertEqual(err.exception.code, 2)
        self.assertFalse(self.editor.exists())

    def test_refuses_to_overwrite_existing_destination(self):
        self.editor.write_text("untouched", encoding="utf-8")
        with self.assertRaises(SystemExit) as err:
            self._export()
        self.assertEqual(err.exception.code, 2)
        self.assertEqual(self.editor.read_text(encoding="utf-8"), "untouched")

    def test_offline_note_edit_requires_fresh_revision_and_anchor(self):
        report = self._export()
        edited = json.loads(self.editor.read_text(encoding="utf-8"))
        edited["nodes"][0]["data"]["notes"]["purpose"] = "edited safely"
        self.editor.unlink()
        self.editor.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._run(["verify", "--input", str(self.editor),
                       "--expected-revision", report["base_revision"],
                       "--expected-sha256", report["base_sha256"],
                       "--output", str(self.candidate), "--allow-private"])
        self.assertFalse(self.candidate.exists())
        result = self._run([
            "verify", "--input", str(self.editor),
            "--expected-revision", report["base_revision"],
            "--expected-sha256", report["base_sha256"],
            "--new-revision", "reviewed-r2",
            "--output", str(self.candidate), "--allow-private",
        ])
        self.assertTrue(result["notes_changed"])
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        self.assertEqual(candidate["graph"]["revision"], "reviewed-r2")
        self.assertEqual(self.original["revision"], "0.1.0-fixture")
        self.assertNotEqual(candidate["graph"]["origin"],
                            self.original["origin"])

    def test_edited_canonical_provenance_rejected(self):
        report = self._export()
        envelope = json.loads(self.editor.read_text(encoding="utf-8"))
        envelope["canonical"]["nodes"][0]["config"]["bpm"] = 77
        self.editor.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(SystemExit) as err:
            self._run([
                "verify", "--input", str(self.editor),
                "--expected-revision", report["base_revision"],
                "--expected-sha256", report["base_sha256"],
                "--output", str(self.candidate), "--allow-private",
            ])
        self.assertEqual(err.exception.code, 2)
        self.assertFalse(self.candidate.exists())


if __name__ == "__main__":
    unittest.main()
