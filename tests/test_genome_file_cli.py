"""Public genome-file admission must finish before any journal mutation."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from mojum.genome_file import MAX_GENOME_BYTES


ROOT = Path(__file__).resolve().parents[1]
SEED_BYTES = (ROOT / "src/mojum/data/genesis_seed.json").read_bytes()
SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")


class GenomeFileCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mojum-genome-cli-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.sqlite3"
        self.backup = self.root / "backup.sqlite3"
        self.genome = self.root / "genome.json"

    def cli(self, module, *arguments):
        # Running outside the checkout also exercises the packaged defaults.
        # The deadline makes a writerless FIFO regression terminate the test.
        return subprocess.run(
            [sys.executable, "-m", "mojum." + module,
             *(str(argument) for argument in arguments)],
            cwd=self.root,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, encoding="utf-8", timeout=8,
        )

    def successful_cli(self, module, *arguments):
        result = self.cli(module, *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def sqlite_files(self, path):
        files = {}
        for suffix in SQLITE_SUFFIXES:
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                files[suffix] = (
                    candidate.read_bytes(), stat.S_IMODE(candidate.stat().st_mode),
                )
        return files

    def existing_fixture(self):
        checkpoint = self.successful_cli(
            "aquarium", "--db", self.source, "--ticks", "1",
        )
        metadata = self.successful_cli(
            "aquarium_cold_copy", "export", "--db", self.source,
            "--output", self.backup,
        )
        self.assertEqual(metadata["journal_head"], checkpoint["journal_head"])
        return metadata

    def assert_rejected_before_journal_access(self, genome, *, diagnostic=None):
        metadata = self.existing_fixture()
        new_db = self.root / "new.sqlite3"
        new_copy = self.root / "rejected-copy.sqlite3"
        originals = {
            self.source: self.sqlite_files(self.source),
            self.backup: self.sqlite_files(self.backup),
        }
        commands = (
            ("aquarium", "--db", new_db, "--ticks", "1"),
            ("aquarium", "--db", self.source, "--ticks", "1"),
            ("aquarium_cold_copy", "export", "--db", self.source,
             "--output", new_copy),
            ("aquarium_cold_copy", "verify", "--db", self.backup,
             "--expected-head", metadata["journal_head"],
             "--expected-sha256", metadata["db_sha256"]),
        )
        for command in commands:
            with self.subTest(command=command[:2]):
                result = self.cli(*command, "--genome", genome)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("--genome", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                if diagnostic is not None:
                    self.assertIn(diagnostic, result.stderr.lower())
                self.assertEqual(self.sqlite_files(new_db), {})
                self.assertEqual(self.sqlite_files(new_copy), {})
                for path, before in originals.items():
                    self.assertEqual(self.sqlite_files(path), before)
                self.assertFalse(list(self.root.glob(".mojum-cold-copy-*")))

    def assert_start_resume_and_cold_roundtrip(self, genome=None):
        options = () if genome is None else ("--genome", genome)
        boot = self.successful_cli(
            "aquarium", "--db", self.source, "--ticks", "2", *options,
        )
        self.assertEqual((boot["tick"], boot["stored_events"]), (2, 3))
        resumed = self.successful_cli(
            "aquarium", "--db", self.source, "--ticks", "1",
            "--expected-head", boot["journal_head"], *options,
        )
        self.assertEqual((resumed["tick"], resumed["stored_events"]), (3, 4))
        continuous = self.successful_cli(
            "aquarium", "--db", self.root / "continuous.sqlite3",
            "--ticks", "3", *options,
        )
        self.assertEqual(resumed, continuous)
        # A successful read-only SQLite backup may create WAL coordination
        # sidecars; the source database bytes and permissions stay intact.
        source_before = self.sqlite_files(self.source)[""]
        exported = self.successful_cli(
            "aquarium_cold_copy", "export", "--db", self.source,
            "--output", self.backup, *options,
        )
        self.assertEqual(exported["journal_head"], resumed["journal_head"])
        self.assertEqual(exported["agent_id"], resumed["agent_id"])
        self.assertEqual((exported["tick"], exported["event_count"]), (3, 4))
        backup_before = self.sqlite_files(self.backup)
        verified = self.successful_cli(
            "aquarium_cold_copy", "verify", "--db", self.backup,
            "--expected-head", exported["journal_head"],
            "--expected-sha256", exported["db_sha256"],
            "--expected-tick", "3", *options,
        )
        self.assertEqual(verified["journal_head"], resumed["journal_head"])
        self.assertEqual(verified["agent_id"], resumed["agent_id"])
        self.assertEqual((verified["tick"], verified["event_count"]), (3, 4))
        self.assertEqual(self.sqlite_files(self.source)[""], source_before)
        self.assertEqual(self.sqlite_files(self.backup), backup_before)

    def test_packaged_default_seed_start_resume_and_cold_roundtrip(self):
        self.assert_start_resume_and_cold_roundtrip()

    def test_regular_seed_start_resume_and_cold_roundtrip(self):
        seed = json.loads(SEED_BYTES)
        seed["genome_id"] = "synthetic-cli-genome-\u043c\u043e\u0434\u0436\u0430\u043c"
        self.genome.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        self.assert_start_resume_and_cold_roundtrip(self.genome)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink admission")
    def test_regular_seed_symlink_start_resume_and_cold_roundtrip(self):
        self.genome.write_bytes(SEED_BYTES)
        linked = self.root / "linked-genome.json"
        linked.symlink_to(self.genome.name)
        self.assert_start_resume_and_cold_roundtrip(linked)

    def test_seed_exactly_at_byte_budget_remains_usable(self):
        self.genome.write_bytes(
            SEED_BYTES + b" " * (MAX_GENOME_BYTES - len(SEED_BYTES)),
        )
        self.assert_start_resume_and_cold_roundtrip(self.genome)

    def test_oversized_valid_seed_rejected_without_touching_journals(self):
        self.genome.write_bytes(
            SEED_BYTES + b" " * (MAX_GENOME_BYTES + 1 - len(SEED_BYTES)),
        )
        self.assert_rejected_before_journal_access(self.genome)

    def test_duplicate_genome_identity_rejected_without_touching_journals(self):
        self.genome.write_bytes(SEED_BYTES.replace(
            b'"genome_id":', b'"genome_id":"other-organism","genome_id":', 1,
        ))
        self.assert_rejected_before_journal_access(self.genome, diagnostic="duplicate")

    def test_duplicate_inherited_gene_rejected_without_touching_journals(self):
        self.genome.write_bytes(SEED_BYTES.replace(
            b'"heartbeat_bpm":', b'"heartbeat_bpm":31,"heartbeat_bpm":', 1,
        ))
        self.assert_rejected_before_journal_access(self.genome, diagnostic="duplicate")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"),
                         "POSIX FIFO admission")
    def test_writerless_fifo_rejected_before_open_can_block(self):
        os.mkfifo(self.genome)
        self.assert_rejected_before_journal_access(self.genome, diagnostic="regular")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"),
                         "POSIX FIFO symlink admission")
    def test_symlink_to_writerless_fifo_rejected_before_open_can_block(self):
        fifo = self.root / "seed.fifo"
        os.mkfifo(fifo)
        self.genome.symlink_to(fifo.name)
        self.assert_rejected_before_journal_access(self.genome, diagnostic="regular")

    @unittest.skipUnless(os.name == "posix" and Path("/dev/null").exists(),
                         "POSIX character device admission")
    def test_device_rejected_as_nonregular_input(self):
        # /dev/null is safe even against a broken implementation; requiring
        # the admission diagnostic proves rejection precedes JSON parsing.
        if not stat.S_ISCHR(Path("/dev/null").stat().st_mode):
            self.skipTest("/dev/null is not a character device")
        self.assert_rejected_before_journal_access(Path("/dev/null"), diagnostic="regular")


if __name__ == "__main__":
    unittest.main()
