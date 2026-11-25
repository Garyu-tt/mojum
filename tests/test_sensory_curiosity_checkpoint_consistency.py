"""Real-journal revision consistency for sensory replay, checkpoints and CLI."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import LocalNoosphere, NoosphereError
from mojum.sensory_curiosity_lab import (
    ContactAssociations, CuriosityError, SensoryPlay, main,
)


ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = ROOT / "examples" / "first_loop.json"
GRAPH = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))


class SensoryCheckpointConsistencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sensory.sqlite3"

    def open(self):
        return SensoryPlay(self.path, GRAPH)

    @staticmethod
    def local_state(baby):
        return (baby.tick, baby.world, baby.model.snapshot(), baby.rng,
                baby._committed_head)

    def test_open_adopts_head_verified_after_another_writer_advances(self):
        with self.open() as writer:
            original_verify = LocalNoosphere.verify_chain
            fired = False

            def advance_then_verify(store, **kwargs):
                nonlocal fired
                if store is not writer.store and not fired:
                    fired = True
                    writer.step()
                return original_verify(store, **kwargs)

            with patch.object(LocalNoosphere, "verify_chain", advance_then_verify):
                with self.open() as reader:
                    self.assertTrue(fired)
                    self.assertEqual(reader.checkpoint(), writer.checkpoint())
                    reader.step()
                    checkpoint = reader.checkpoint()
                    verified = original_verify(reader.store)
                    self.assertEqual(checkpoint["tick"], 2)
                    self.assertEqual(checkpoint["head"], verified["head"])
                    self.assertEqual(verified["count"], checkpoint["tick"] + 1)

    def test_open_rejects_append_between_chain_verification_and_projection(self):
        with self.open() as writer:
            original_verify = LocalNoosphere.verify_chain
            fired = False

            def verify_then_advance(store, **kwargs):
                nonlocal fired
                receipt = original_verify(store, **kwargs)
                if store is not writer.store and not fired:
                    fired = True
                    writer.step()
                return receipt

            with patch.object(LocalNoosphere, "verify_chain", verify_then_advance):
                with self.assertRaises(CuriosityError):
                    with self.open():
                        pass
            self.assertTrue(fired)
            self.assertEqual(writer.store.count(), 2)
            with self.open() as recovered:
                self.assertEqual(recovered.checkpoint(), writer.checkpoint())

    def test_open_rejects_append_after_projection_is_materialized(self):
        with self.open() as writer:
            writer.step()
            original_dataset = LocalNoosphere.dataset
            fired = False

            def project_then_advance(store, **kwargs):
                nonlocal fired
                projection = original_dataset(store, **kwargs)
                if store is not writer.store and not fired:
                    fired = True
                    writer.step()
                return projection

            with patch.object(LocalNoosphere, "dataset", project_then_advance):
                with self.assertRaises(CuriosityError):
                    with self.open():
                        pass
            self.assertTrue(fired)
            self.assertEqual(writer.store.count(), 3)
            with self.open() as recovered:
                self.assertEqual(recovered.checkpoint(), writer.checkpoint())

    def test_open_rejects_projection_tail_outside_verified_chain(self):
        with self.open() as writer:
            writer.step()
            before = writer.checkpoint()
            original_dataset = LocalNoosphere.dataset
            fired = False

            def change_tail_hash(store, **kwargs):
                nonlocal fired
                projection = original_dataset(store, **kwargs)
                if store is not writer.store:
                    fired = True
                    projection["projection"]["items"][-1]["event_hash"] = "0" * 64
                return projection

            with patch.object(LocalNoosphere, "dataset", change_tail_hash):
                with self.assertRaises(CuriosityError):
                    with self.open():
                        pass
            self.assertTrue(fired)
            self.assertEqual(writer.checkpoint(), before)
            self.assertEqual(writer.store.verify_chain()["head"], before["head"])

    def test_checkpoint_rechecks_head_after_association_snapshot(self):
        with self.open() as writer, self.open() as reader:
            before = self.local_state(reader)
            original_snapshot = ContactAssociations.snapshot
            fired = False

            def snapshot_then_advance(model):
                nonlocal fired
                snapshot = original_snapshot(model)
                if model is reader.model and not fired:
                    fired = True
                    writer.step()
                return snapshot

            with patch.object(ContactAssociations, "snapshot", snapshot_then_advance):
                with self.assertRaisesRegex(CuriosityError, "stale sensory checkpoint"):
                    reader.checkpoint()
            self.assertTrue(fired)
            self.assertEqual(self.local_state(reader), before)
            self.assertEqual(writer.store.count(), 2)

    def test_cli_count_belongs_to_returned_checkpoint_after_later_append(self):
        with self.open() as writer:
            before = writer.checkpoint()
            original_checkpoint = SensoryPlay.checkpoint
            fired = False

            def checkpoint_then_advance(baby):
                nonlocal fired
                checkpoint = original_checkpoint(baby)
                if baby is not writer and not fired:
                    fired = True
                    writer.step()
                return checkpoint

            output = io.StringIO()
            argv = ["sensory_curiosity_lab", "--db", str(self.path),
                    "--graph", str(GRAPH_PATH), "--ticks", "0"]
            with patch.object(SensoryPlay, "checkpoint", checkpoint_then_advance), \
                    patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                main()
            receipt = json.loads(output.getvalue())
            self.assertTrue(fired)
            self.assertEqual(receipt["checkpoint"], before)
            self.assertEqual(receipt["private_events"], before["tick"] + 1)
            self.assertEqual(writer.store.count(), 2)
            self.assertNotEqual(receipt["checkpoint"]["head"], writer.checkpoint()["head"])

    def test_stale_append_preserves_local_state_and_committed_history(self):
        with self.open() as writer, self.open() as stale:
            before = self.local_state(stale)
            writer.step()
            writer.step()
            committed = writer.store.verify_chain()
            with self.assertRaisesRegex(CuriosityError, "stale sensory checkpoint"):
                stale.checkpoint()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(self.local_state(stale), before)
            self.assertEqual(writer.store.verify_chain(), committed)

    def test_failed_append_does_not_publish_candidate_state(self):
        with self.open() as baby:
            baby.step()
            before = baby.checkpoint()
            local_before = self.local_state(baby)
            committed = baby.store.verify_chain()
            with patch.object(baby.store, "append", side_effect=NoosphereError("write failed")):
                with self.assertRaisesRegex(NoosphereError, "write failed"):
                    baby.step()
            self.assertEqual(self.local_state(baby), local_before)
            self.assertEqual(baby.checkpoint(), before)
            self.assertEqual(baby.store.verify_chain(), committed)

    def test_cold_resume_and_continuation_match_uninterrupted_history(self):
        control_path = self.path.with_name("control.sqlite3")
        with SensoryPlay(control_path, GRAPH) as control:
            expected_frames = [control.step() for _ in range(40)]
            expected_checkpoint = control.checkpoint()
            expected_receipt = control.store.verify_chain()
        with self.open() as baby:
            self.assertEqual([baby.step() for _ in range(25)], expected_frames[:25])
            before_close = baby.checkpoint()
        with self.open() as resumed:
            self.assertEqual(resumed.checkpoint(), before_close)
            self.assertEqual([resumed.step() for _ in range(15)], expected_frames[25:])
            self.assertEqual(resumed.checkpoint(), expected_checkpoint)
            self.assertEqual(resumed.store.verify_chain(), expected_receipt)
            self.assertEqual(resumed.store.count(), expected_checkpoint["tick"] + 1)


if __name__ == "__main__":
    unittest.main()
