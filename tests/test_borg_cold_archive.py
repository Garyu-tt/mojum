"""CPU-only integration-contract tests. Borg itself is mocked; no credentials needed."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.borg_cold_archive import (
    BorgBackupError, archive_cold_copy, restore_cold_copy,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
OWNER = SEED["genome_id"]


class FakeBorg:
    """Minimal fake of Borg's CLI contract, including encrypted repo metadata."""

    def __init__(self, mode="repokey"):
        self.archives = {}
        self.mode = mode
        self.version = "borg 1.4.5"
        self.fail_on = None
        self.calls = []

    def __call__(self, args, *, cwd=None):
        self.calls.append((tuple(args), cwd))
        if args[0] == self.fail_on:
            raise BorgBackupError("Borg operation failed; inspect trusted local logs")
        if args == ["--version"]:
            return self.version + "\n"
        if args[:2] == ["info", "--json"]:
            return json.dumps({"encryption": {"mode": self.mode},
                               "repository": {"id": "f" * 64}})
        if args[0] == "create":
            name = args[-2].split("::", 1)[1]
            assert args[-1] == "private.sqlite3"
            self.archives[name] = (Path(cwd) / "private.sqlite3").read_bytes()
            return ""
        if args[0] == "check":
            assert args[1] == "--verify-data"
            assert args[-1].split("::", 1)[1] in self.archives
            return ""
        if args[0] == "extract":
            name = args[1].split("::", 1)[1]
            (Path(cwd) / "private.sqlite3").write_bytes(self.archives[name])
            return ""
        raise AssertionError(f"unexpected Borg command {args}")


class BorgColdArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "encrypted-repo"
        self.repo.mkdir()
        self.source = self.root / "live.sqlite3"
        self.restored = self.root / "recovered.sqlite3"
        self.fake = FakeBorg()
        self.patched = patch("mojum.borg_cold_archive._run", side_effect=self.fake)
        self.patched.start()
        self.addCleanup(self.patched.stop)

    def _make_journal(self, *, ticks=4):
        with Aquarium(self.source, SEED, GRAPH) as child:
            for _ in range(ticks):
                child.step(dx=0.01)
            return child.checkpoint()

    def _archive(self, name="snapshot-001"):
        return archive_cold_copy(
            self.source, self.repo, name, SEED, GRAPH, agent_id=OWNER,
        )

    def _restore(self, meta, name=None, destination=None, **overrides):
        params = {
            "agent_id": OWNER, "expected_head": meta["journal_head"],
            "expected_sha256": meta["db_sha256"],
            "expected_tick": meta["tick"],
        }
        params.update(overrides)
        return restore_cold_copy(
            self.repo, name or meta["archive"],
            destination or self.restored, SEED, GRAPH, **params,
        )

    def test_wal_consistent_archive_roundtrip_and_source_untouched(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            for _ in range(6):
                child.step(dx=0.02)
            original = child.checkpoint()
            before = self.source.read_bytes()
            meta = self._archive()
            self.assertEqual(self.source.read_bytes(), before)
            self.assertEqual(meta["journal_head"], original["journal_head"])
            self.assertEqual(meta["tick"], 6)
            result = self._restore(meta)
            self.assertEqual(result["tick"], 6)
            self.assertEqual(result["db_sha256"], meta["db_sha256"])
            with Aquarium(self.restored, SEED, GRAPH, read_only=True) as restored:
                self.assertEqual(restored.checkpoint(), original)
            self.assertEqual(child.checkpoint(), original)
        self.assertEqual(self.restored.stat().st_mode & 0o777, 0o600)

    def test_encrypted_repository_is_mandatory(self):
        self._make_journal()
        self.fake.mode = "none"
        with self.assertRaisesRegex(BorgBackupError, "authenticated encryption"):
            self._archive()
        self.assertEqual(self.fake.archives, {})
        self.fake.mode = "authenticated"
        with self.assertRaisesRegex(BorgBackupError, "authenticated encryption"):
            self._archive()

    def test_wrong_version_and_missing_metadata_fail_closed(self):
        self._make_journal()
        self.fake.version = "borg 2.0.0"
        with self.assertRaisesRegex(BorgBackupError, "version"):
            self._archive()
        self.fake.version = "borg 1.4.5"
        self.fake.mode = None
        with self.assertRaisesRegex(BorgBackupError, "authenticated encryption"):
            self._archive()

    def test_corrupted_archived_bytes_refuse_restore(self):
        self._make_journal()
        meta = self._archive()
        raw = bytearray(self.fake.archives[meta["archive"]])
        raw[100] ^= 255
        self.fake.archives[meta["archive"]] = bytes(raw)
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self._restore(meta)
        self.assertFalse(self.restored.exists())
        self.assertFalse(list(self.root.glob(".mojum-borg-restore-*")))

    def test_wrong_independent_head_rejects_publish(self):
        self._make_journal()
        meta = self._archive()
        with self.assertRaises(ValueError):
            self._restore(meta, expected_head="0" * 64)
        self.assertFalse(self.restored.exists())

    def test_failed_archive_does_not_claim_success(self):
        self._make_journal()
        self.fake.fail_on = "check"
        with self.assertRaises(BorgBackupError):
            self._archive()
        self.assertFalse(list(self.root.glob("mojum-borg-stage-*")))

    def test_never_clobber_existing_restore_and_sidecars(self):
        self._make_journal()
        meta = self._archive()
        self.restored.write_bytes(b"untouched")
        with self.assertRaises(FileExistsError):
            self._restore(meta)
        self.assertEqual(self.restored.read_bytes(), b"untouched")
        self.restored.unlink()
        sidecar = Path(str(self.restored) + "-wal")
        sidecar.write_bytes(b"other")
        with self.assertRaises(FileExistsError):
            self._restore(meta)
        self.assertFalse(self.restored.exists())
        self.assertEqual(sidecar.read_bytes(), b"other")

    def test_reject_remote_repo_unsafe_archive_and_symlink(self):
        self._make_journal()
        with self.assertRaises(BorgBackupError):
            archive_cold_copy(self.source, "ssh://host/private", "valid", SEED,
                              GRAPH, agent_id=OWNER)
        for name in ("../escape", "bad::repo", "--danger", "", "a" * 100):
            with self.subTest(name=name), self.assertRaises(BorgBackupError):
                archive_cold_copy(self.source, self.repo, name, SEED,
                                  GRAPH, agent_id=OWNER)
        link = self.root / "link"
        link.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(BorgBackupError):
            archive_cold_copy(self.source, link, "valid", SEED, GRAPH,
                              agent_id=OWNER)

    def test_second_archive_can_recover_after_first_corrupted(self):
        self._make_journal()
        # Two independently initialized *local repo paths*, not two
        # archives within the same Borg repository. Different host/failure
        # domain recovery is still beyond this controlled fake-Borg test.
        repo2 = self.root / "second-repository"
        repo2.mkdir()
        a = self._archive("copy-a")
        b = archive_cold_copy(self.source, repo2, "copy-b", SEED, GRAPH,
                              agent_id=OWNER)
        self.fake.archives["copy-a"] = b"broken"
        with self.assertRaises(ValueError):
            self._restore(a, destination=self.root / "bad.sqlite3")
        self.assertFalse((self.root / "bad.sqlite3").exists())
        recovered = restore_cold_copy(
            repo2, "copy-b", self.restored, SEED, GRAPH,
            agent_id=OWNER, expected_head=a["journal_head"],
            expected_sha256=a["db_sha256"], expected_tick=a["tick"],
        )
        self.assertEqual(recovered["journal_head"], b["journal_head"])
        self.assertEqual(recovered["tick"], 4)


if __name__ == "__main__":
    unittest.main()
