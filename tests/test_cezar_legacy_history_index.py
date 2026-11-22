"""No-network, adversarial tests for archived Cezar history dedup index."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import os
import tarfile
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout, redirect_stderr

MODULE = Path(__file__).resolve().parents[1] / "tools" / "cezar_legacy_history_index.py"
spec = importlib.util.spec_from_file_location("cezar_legacy_history_index", MODULE)
indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexer)


class LegacyHistoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / "historical.tar.gz"
        self.outdir = self.root / "private"
        self.outdir.mkdir(mode=0o700)
        self.out = self.outdir / "metadata.json"

    def _run(self, task, number, status="done"):
        return {
            "id": str(uuid.UUID(int=number)),
            "title": "private historical title",
            "task": task,
            "status": status,
            "createdAt": "2026-11-16T00:00:00Z",
            "runner": "claude",
        }

    def make_archive(self, projects=None, *, bad_type=None, duplicate=False):
        projects = projects or {
            indexer.PROJECTS[0]: [
                self._run("PRIVATE_TASK_TEXT_ALPHA", 1),
                self._run("PRIVATE_TASK_TEXT_ALPHA", 2),
                self._run("PRIVATE_TASK_TEXT_BETA", 3),
            ],
            indexer.PROJECTS[1]: [self._run("PRIVATE_TASK_TEXT_ALPHA", 4)],
            indexer.PROJECTS[2]: [self._run("PRIVATE_TASK_TEXT_BETA", 5)],
        }
        with tarfile.open(self.archive, "w:gz") as tar:
            for project, rows in projects.items():
                name = f"projects/{project}/.ai/cezar/runs.json"
                raw = json.dumps({"runs": rows}).encode()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                member.mode = 0o600
                if bad_type == project:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/etc/passwd"
                    tar.addfile(member)
                else:
                    tar.addfile(member, io.BytesIO(raw))
                    if duplicate and project == indexer.PROJECTS[0]:
                        tar.addfile(member, io.BytesIO(raw))
        self.archive.chmod(0o600)

    def test_same_task_within_project_collapses_but_cross_project_does_not(self):
        self.make_archive()
        x = indexer.build_index(self.archive)
        self.assertEqual(x["total_runs"], 5)
        self.assertEqual(x["unique_project_tasks"], 4)
        self.assertEqual(x["duplicate_attempts"], 1)
        self.assertEqual(x["status_counts"], {"done": 5})
        self.assertEqual(x["live_cezar_tasks_created"], 0)
        self.assertEqual(sum(y["repeats"] for y in x["groups"]), 1)
        self.assertFalse("PRIVATE_TASK_TEXT" in json.dumps(x))
        self.assertFalse("private historical title" in json.dumps(x))
        for group in x["groups"]:
            self.assertEqual(len(group["run_ids"]), group["run_count"])
            self.assertEqual(len(group["key"]), 64)

    def test_owner_only_atomic_index_write_is_idempotent(self):
        self.make_archive()
        x = indexer.build_index(self.archive)
        indexer.save_index(x, self.out)
        indexer.save_index(x, self.out)
        self.assertEqual(json.loads(self.out.read_text()), x)
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)
        self.assertTrue(x["read_only_legacy"])
        self.assertFalse(x["prompts_or_secrets_exported"])

    def test_cli_only_prints_metadata_summary(self):
        self.make_archive()
        output = io.StringIO()
        with redirect_stdout(output):
            code = indexer.main(["--archive", str(self.archive),
                                 "--output", str(self.out)])
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["duplicate_attempts"], 1)
        self.assertEqual(summary["live_cezar_tasks_created"], 0)
        self.assertNotIn("PRIVATE_TASK_TEXT", output.getvalue())

    def test_symlink_member_and_duplicate_member_rejected(self):
        self.make_archive(bad_type=indexer.PROJECTS[1])
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)
        self.make_archive(duplicate=True)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)

    def test_missing_projects_and_invalid_run_identity_rejected(self):
        self.make_archive(projects={indexer.PROJECTS[0]: [self._run("task", 1)]})
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)
        rows = {project: [self._run("task", n + 1)]
                for n, project in enumerate(indexer.PROJECTS)}
        rows[indexer.PROJECTS[1]][0]["id"] = "not-uuid"
        self.make_archive(projects=rows)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)

    def test_duplicates_of_run_uuid_and_bad_task_fail_closed(self):
        rows = {project: [self._run("task", n + 1)]
                for n, project in enumerate(indexer.PROJECTS)}
        rows[indexer.PROJECTS[1]][0]["id"] = rows[indexer.PROJECTS[0]][0]["id"]
        self.make_archive(projects=rows)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)
        rows[indexer.PROJECTS[1]][0]["id"] = str(uuid.UUID(int=2))
        rows[indexer.PROJECTS[1]][0]["task"] = ""
        self.make_archive(projects=rows)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)

    def test_world_readable_file_and_folder_refused(self):
        self.make_archive()
        self.archive.chmod(0o644)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(self.archive)
        self.archive.chmod(0o600)
        x = indexer.build_index(self.archive)
        self.outdir.chmod(0o755)
        with self.assertRaises(indexer.IndexRejected):
            indexer.save_index(x, self.out)

    def test_symlink_source_or_target_directory_refused(self):
        self.make_archive()
        alias = self.root / "source-alias"
        alias.symlink_to(self.archive)
        with self.assertRaises(indexer.IndexRejected):
            indexer.build_index(alias)
        x = indexer.build_index(self.archive)
        alias_dir = self.root / "private-alias"
        alias_dir.symlink_to(self.outdir, target_is_directory=True)
        with self.assertRaises(indexer.IndexRejected):
            indexer.save_index(x, alias_dir / "index.json")

    def test_unexpected_existing_index_is_not_overwritten(self):
        self.make_archive()
        x = indexer.build_index(self.archive)
        self.out.write_text("DO_NOT_DELETE")
        self.out.chmod(0o644)
        with self.assertRaises(indexer.IndexRejected):
            indexer.save_index(x, self.out)
        self.assertEqual(self.out.read_text(), "DO_NOT_DELETE")


if __name__ == "__main__":
    unittest.main()
