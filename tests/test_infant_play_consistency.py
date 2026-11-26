"""Infant checkpoints and writes must name the replayed SQLite prefix."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.infant_play import InfantError, InfantPlay, main
from mojum.noosphere import LocalNoosphere, NoosphereError


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "examples/first_loop.json"
GRAPH = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))


class InfantConsistencyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "infant.sqlite3"

    def open(self):
        return InfantPlay(self.db, GRAPH)

    def test_stale_checkpoint_rejects_another_writers_head(self):
        with self.open() as writer, self.open() as stale:
            before = stale.checkpoint()
            writer.step()
            after = writer.checkpoint()
            with self.assertRaisesRegex(InfantError, "stale.*checkpoint"):
                stale.checkpoint()
            self.assertEqual(stale.tick, before["tick"])
            self.assertEqual(stale.model.snapshot(), before["motor_memory"])
        with self.open() as reopened:
            self.assertEqual(reopened.checkpoint(), after)

    def test_checkpoint_rechecks_after_building_local_state(self):
        with self.open() as writer, self.open() as stale:
            snapshot = stale.model.snapshot

            def snapshot_then_advance():
                value = snapshot()
                writer.step()
                return value

            with patch.object(stale.model, "snapshot", snapshot_then_advance):
                with self.assertRaisesRegex(InfantError, "stale.*checkpoint"):
                    stale.checkpoint()
            self.assertEqual(stale.tick, 0)
            self.assertEqual(writer.tick, 1)

    def test_stale_step_uses_original_head_for_cas(self):
        with self.open() as writer, self.open() as stale:
            before = stale.checkpoint()
            writer.step()
            after = writer.checkpoint()
            with self.assertRaisesRegex(NoosphereError, "CAS failed"):
                stale.step()
            self.assertEqual(stale.tick, before["tick"])
            self.assertEqual(stale.world.revision, before["world_revision"])
            self.assertEqual(stale.model.snapshot(), before["motor_memory"])
            self.assertEqual(writer.checkpoint(), after)
            self.assertEqual(writer.store.count(), 2)

    def test_failed_append_preserves_checkpoint_and_restart(self):
        with self.open() as infant:
            infant.step()
            before = infant.checkpoint()
            with patch.object(infant.store, "append", side_effect=NoosphereError("failed write")):
                with self.assertRaisesRegex(NoosphereError, "failed write"):
                    infant.step()
            self.assertEqual(infant.checkpoint(), before)
            self.assertEqual(infant.store.count(), 2)
        with self.open() as reopened:
            self.assertEqual(reopened.checkpoint(), before)

    def test_replay_keeps_the_reconstructed_prefix_after_later_append(self):
        dataset = LocalNoosphere.dataset
        with self.open() as writer:
            def read_then_advance(store, **kwargs):
                rows = dataset(store, **kwargs)
                writer.step()
                return rows

            with patch.object(LocalNoosphere, "dataset", read_then_advance):
                reader = self.open()
            with reader:
                self.assertEqual(reader.tick, 0)
                self.assertEqual(writer.tick, 1)
                with self.assertRaisesRegex(InfantError, "stale.*checkpoint"):
                    reader.checkpoint()
            with self.open() as reopened:
                self.assertEqual(reopened.checkpoint(), writer.checkpoint())

    def test_replay_rejects_tail_added_after_chain_verification(self):
        verify_chain = LocalNoosphere.verify_chain
        with self.open() as writer:
            def verify_then_advance(store, **kwargs):
                verified = verify_chain(store, **kwargs)
                writer.step()
                return verified

            with patch.object(LocalNoosphere, "verify_chain", verify_then_advance):
                with self.assertRaisesRegex(InfantError, "advanced during replay"):
                    with self.open():
                        pass
            with self.open() as reopened:
                self.assertEqual(reopened.checkpoint(), writer.checkpoint())

    def test_cli_event_count_belongs_to_reported_checkpoint(self):
        checkpoint = InfantPlay.checkpoint

        def checkpoint_then_advance(infant):
            value = checkpoint(infant)
            with self.open() as writer:
                writer.step()
            return value

        output = StringIO()
        command = ["infant-play", "--db", str(self.db), "--ticks", "2",
                   "--graph", str(GRAPH_PATH)]
        with (
            patch.object(sys, "argv", command),
            patch.object(InfantPlay, "checkpoint", checkpoint_then_advance),
            redirect_stdout(output),
        ):
            main()
        reported = json.loads(output.getvalue())
        self.assertEqual((reported["checkpoint"]["tick"], reported["events"]), (2, 3))
        with self.open() as reopened:
            self.assertEqual((reopened.tick, reopened.store.count()), (3, 4))
            rows = reopened.store.dataset(include_nonpublic=True)["projection"]["items"]
            prefix = next(row for row in rows if
                          row["event_hash"] == reported["checkpoint"]["journal_head"])
            self.assertEqual(prefix["seq"], reported["events"])


if __name__ == "__main__":
    unittest.main()
