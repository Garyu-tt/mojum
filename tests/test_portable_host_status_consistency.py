"""Foreground status must describe one validated committed journal prefix."""

from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, V4_MODE
from mojum.portable_host import main as host_main


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
PROFILES = (
    ("developmental_fixture", "toy_probe", "range_guided"),
    (V4_MODE, "none", "none"),
    (V4_MODE, "toy_probe_v2", "none"),
)


class PortableHostStatusConsistencyTests(unittest.TestCase):
    def _options(self, profile):
        motor, sensory, temporal = profile
        return {
            "motor_mode": motor,
            "sensory_mode": sensory,
            "temporal_mode": temporal,
            "neural_mode": "disabled",
        }

    def _argv(self, path, profile):
        argv = ["--db", str(path), "--ticks", "2", "--interval-ms", "20"]
        for name, value in self._options(profile).items():
            argv.extend(("--" + name.replace("_", "-"), value))
        return argv

    def _exercise_late_writer(self, profile, *, with_development=False):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "synthetic-private.sqlite3"
            options = self._options(profile)
            if with_development:
                with Aquarium(path, SEED, GRAPH, **options) as setup:
                    setup.advance_development(2)

            original_checkpoint = Aquarium.checkpoint
            later = {}

            def checkpoint_then_append(aquarium):
                checkpoint = original_checkpoint(aquarium)
                # A real independent WAL connection commits after the host's
                # final head check, before its status object is constructed.
                with Aquarium(path, SEED, GRAPH, **options) as writer:
                    writer.step()
                    later.update(original_checkpoint(writer))
                return checkpoint

            stdout = StringIO()
            with patch.object(Aquarium, "checkpoint", checkpoint_then_append), \
                    redirect_stdout(stdout):
                status = host_main(self._argv(path, profile))

            self.assertEqual(json.loads(stdout.getvalue()), status)
            self.assertEqual((status["start_tick"], status["end_tick"],
                              status["committed_ticks"]), (0, 2, 2))
            self.assertEqual(status["stop_reason"], "completed")
            self.assertNotEqual(status["journal_head"], later["journal_head"])

            # Resolve the reported head in the real persisted chain. The
            # matching sequence counts bootstrap and non-frame events too;
            # it cannot be inferred from the virtual tick alone.
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                reported_sequence = db.execute(
                    "SELECT seq FROM events WHERE event_hash = ?",
                    (status["journal_head"],),
                ).fetchone()[0]
                current_count = db.execute("SELECT count(*) FROM events").fetchone()[0]
            self.assertEqual(status["journal_events"], reported_sequence)
            self.assertEqual(reported_sequence, 4 if with_development else 3)
            self.assertEqual(current_count, reported_sequence + 1)

            with Aquarium(path, SEED, GRAPH, **options) as reopened:
                self.assertEqual(reopened.tick, 3)
                self.assertEqual(reopened.checkpoint(), later)
                self.assertEqual(reopened.stored_events, current_count)
                if with_development:
                    self.assertEqual(reopened.development_months, 74)

    def test_later_writer_cannot_mix_event_count_into_validated_status(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self._exercise_late_writer(profile)

    def test_status_counts_nonframe_events_in_its_validated_prefix(self):
        self._exercise_late_writer(PROFILES[0], with_development=True)

    def test_writer_advancing_before_checkpoint_still_rejects_stale_status(self):
        for profile in PROFILES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / "synthetic-private.sqlite3"
                options = self._options(profile)
                original_checkpoint = Aquarium.checkpoint

                def append_then_checkpoint(aquarium):
                    with Aquarium(path, SEED, GRAPH, **options) as writer:
                        writer.step()
                    return original_checkpoint(aquarium)

                stdout, stderr = StringIO(), StringIO()
                with patch.object(Aquarium, "checkpoint", append_then_checkpoint), \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as rejected:
                        host_main(self._argv(path, profile))
                self.assertEqual(rejected.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("stale aquarium checkpoint", stderr.getvalue())
                with Aquarium(path, SEED, GRAPH, **options) as reopened:
                    self.assertEqual((reopened.tick, reopened.stored_events), (3, 4))


if __name__ == "__main__":
    unittest.main()
