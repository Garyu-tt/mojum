"""A normalized SQLite memory sentinel must never masquerade as a disk journal."""

from contextlib import chdir
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "src/mojum/data/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "src/mojum/data/first_loop.json").read_text(encoding="utf-8"))
MEMORY_ALIASES = (":memory:", "./:memory:", "././:memory:", ":memory:/", "./:memory:/")


class FileArgument(os.PathLike):
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return self.path

    def __str__(self):
        return "display-only-path-label"


class NoospherePathAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_memory_aliases_rejected_before_sqlite_connection(self):
        aliases = (*MEMORY_ALIASES, Path("./:memory:"), FileArgument("./:memory:"))
        with chdir(self.root):
            for path in aliases:
                for read_only in (False, True):
                    with self.subTest(path=repr(path), read_only=read_only):
                        with patch("mojum.noosphere.sqlite3.connect",
                                   wraps=sqlite3.connect) as connect:
                            with self.assertRaisesRegex(NoosphereError, "persistent SQLite"):
                                with LocalNoosphere(path, SEED["genome_id"], read_only=read_only):
                                    pass
                            connect.assert_not_called()
                        self.assertEqual(list(self.root.iterdir()), [])

    def test_aquarium_refuses_ephemeral_aliases_before_bootstrap(self):
        with chdir(self.root):
            for path in (*MEMORY_ALIASES, FileArgument("./:memory:")):
                with self.subTest(path=repr(path)):
                    with self.assertRaisesRegex(NoosphereError, "persistent SQLite"):
                        with Aquarium(path, SEED, GRAPH, neural_mode="disabled") as aquarium:
                            aquarium.step()
                    self.assertEqual(list(self.root.iterdir()), [])

    def test_ordinary_paths_retain_committed_events_across_reopen(self):
        paths = ["./relative.sqlite3", self.root / "absolute.sqlite3",
                 FileArgument("./pathlike.sqlite3")]
        if os.name == "posix":
            # An explicit absolute filename is not SQLite's special address.
            paths.append(self.root / ":memory:")
        event = {
            "schema": EVENT_SCHEMA, "event_id": "synthetic-event",
            "agent_id": SEED["genome_id"], "topic": "path.persistence",
            "recorded_at_ns": 1, "observed_at_ns": 1, "valid_from_ns": 1,
            "valid_to_ns": None, "visibility": "PRIVATE",
            "source": "fixture:path", "source_revision": "synthetic-1",
            "payload": {"value": 1},
        }
        with chdir(self.root):
            for path in paths:
                with self.subTest(path=repr(path)):
                    with LocalNoosphere(path, SEED["genome_id"]) as store:
                        head = store.append(event, expected_head=store.head())
                    persisted = Path(path)
                    self.assertTrue(persisted.is_file())
                    before = persisted.read_bytes()
                    with LocalNoosphere(path, SEED["genome_id"], read_only=True) as reopened:
                        self.assertEqual(reopened.count(), 1)
                        self.assertEqual(reopened.head(), head)
                        self.assertEqual(reopened.verify_chain(external_expected_head=head)["count"], 1)
                        items = reopened.dataset(include_nonpublic=True)["projection"]["items"]
                        self.assertEqual([item["event"] for item in items], [event])
                    self.assertEqual(persisted.read_bytes(), before)

    def run_cli(self, path, ticks, *options):
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(path),
             "--ticks", str(ticks), "--neural-mode", "disabled", *options],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, timeout=30, check=False,
        )

    def test_cli_alias_rejection_creates_no_journal_or_success_output(self):
        for path in MEMORY_ALIASES:
            with self.subTest(path=path):
                result = self.run_cli(path, 1)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("persistent SQLite", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_resumes_relative_disk_journal_through_absolute_path(self):
        first = self.run_cli("./persistent.sqlite3", 2)
        self.assertEqual(first.returncode, 0, first.stderr)
        checkpoint = json.loads(first.stdout)
        self.assertEqual((checkpoint["tick"], checkpoint["stored_events"]), (2, 3))
        path = self.root / "persistent.sqlite3"
        self.assertTrue(path.is_file())
        resumed = self.run_cli(path, 1, "--expected-head", checkpoint["journal_head"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        continued = json.loads(resumed.stdout)
        self.assertEqual((continued["tick"], continued["stored_events"]), (3, 4))
        with Aquarium(path, SEED, GRAPH, neural_mode="disabled",
                      external_expected_head=continued["journal_head"], read_only=True) as replayed:
            self.assertEqual(replayed.tick, 3)
            self.assertEqual(replayed.stored_events, 4)
            self.assertEqual(replayed.checkpoint()["journal_head"], continued["journal_head"])


if __name__ == "__main__":
    unittest.main()
