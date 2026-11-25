"""Frozen real-writer evidence for the unchanged v3-escape journal format.

The synthetic fixture was emitted by main 310909c4d273dd1f5272017e2c93d0d3a6d2a0ba.
Its seven canonical events include a measured rest at tick four. Additional
frame and journal hashes pin continuation through tick eleven. Neither git nor
an old package installation is needed to run these regressions.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, V3_ESCAPE_MODE
from mojum.noosphere import EMPTY_HEAD, LocalNoosphere, NoosphereError


FIXTURE = Path(__file__).with_name("fixtures") / "v3_escape_legacy_journal.json"
FIXTURE_SHA256 = "c165e742098aee0c0edff29982c6d6270a557318aea079e25f8dea1d6a17907d"


def _digest(value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


class LegacyEscapeJournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_bytes())
        # Pin content, allowing normal LF/CRLF checkout conversion on Windows.
        self.assertEqual(_digest(self.fixture), FIXTURE_SHA256)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.agent_id = self.fixture["seed"]["genome_id"]

    def open(self, path, **kwargs):
        return Aquarium(
            path, self.fixture["seed"], self.fixture["graph"],
            agent_id=self.agent_id, motor_mode=V3_ESCAPE_MODE,
            neural_mode="disabled", **kwargs,
        )

    def restore(self, path, frames=6):
        head = EMPTY_HEAD
        with LocalNoosphere(path, self.agent_id) as journal:
            for row in self.fixture["events"][:frames + 1]:
                head = journal.append(row["event"], expected_head=head)
                self.assertEqual(head, row["event_hash"])
            self.assertEqual(journal.count(), frames + 1)
            journal.verify_chain(external_expected_head=head, strict_wire=True)
        return head

    def assert_frame(self, frame, journal_head):
        tick = frame["tick"]
        self.assertEqual(_digest(frame), self.fixture["frame_sha256"][tick])
        self.assertEqual(journal_head, self.fixture["journal_heads"][tick + 1])

    def test_historical_rest_prefix_resumes_continues_and_reopens(self):
        path = self.root / "historical.sqlite3"
        # End on a real nonzero rest streak, not merely the empty snapshot.
        anchor = self.restore(path, frames=5)
        before = path.read_bytes()
        with self.open(path, external_expected_head=anchor, read_only=True) as reader:
            state = reader.motor.snapshot()
            self.assertEqual(reader.tick, 5)
            self.assertEqual(state["snapshot_version"], 2)
            self.assertEqual(state["consecutive_rests"], 1)
            self.assertTrue(state["history_complete"])
            self.assertEqual([item["tick"] for item in state["outcome_tail"]],
                             [1, 2, 3, 4])
            with self.assertRaisesRegex(AquariumError, "read-only"):
                reader.step()
        self.assertEqual(path.read_bytes(), before)

        with self.open(path, external_expected_head=anchor) as resumed:
            frame = resumed.step()
            self.assertEqual(frame, self.fixture["events"][6]["event"]["payload"])
            self.assert_frame(frame, resumed.checkpoint()["journal_head"])
            anchor = resumed.checkpoint()["journal_head"]
        # Repeated cold resume crosses another rest at tick nine and checks
        # the unchanged frame format, full hash chain and current chronology.
        for tick in range(6, 12):
            with self.open(path, external_expected_head=anchor) as resumed:
                self.assertEqual(resumed.tick, tick)
                frame = resumed.step()
                anchor = resumed.checkpoint()["journal_head"]
                self.assert_frame(frame, anchor)
        with self.open(path, external_expected_head=anchor, read_only=True) as final:
            self.assertEqual(final.tick, 12)
            self.assertEqual(final.stored_events, 13)

    def test_new_writer_preserves_exact_historical_frames_and_chain(self):
        # This golden comparison pins backward-compatible wire output; it
        # does not call the implementation's new journal_snapshot helper to
        # calculate its expectations.
        path = self.root / "new.sqlite3"
        with self.open(path) as writer:
            self.assertEqual(writer.manifest,
                             self.fixture["events"][0]["event"]["payload"])
            self.assertEqual(writer.store.head(), self.fixture["journal_heads"][0])
            for _ in range(12):
                frame = writer.step()
                self.assert_frame(frame, writer.checkpoint()["journal_head"])
            anchor = writer.checkpoint()["journal_head"]
        with self.open(path, external_expected_head=anchor, read_only=True) as reader:
            self.assertEqual(reader.tick, 12)

    def test_wrong_anchor_leaves_historical_and_new_journals_unchanged(self):
        for historical in (True, False):
            with self.subTest(historical=historical):
                path = self.root / ("old.sqlite3" if historical else "new.sqlite3")
                if historical:
                    anchor = self.restore(path)
                else:
                    with self.open(path) as writer:
                        for _ in range(6):
                            writer.step()
                        anchor = writer.checkpoint()["journal_head"]
                before = path.read_bytes()
                permissions = path.stat().st_mode
                for read_only in (True, False):
                    with self.subTest(read_only=read_only):
                        with self.assertRaisesRegex(NoosphereError, "external anchor"):
                            self.open(path, external_expected_head="f" * 64,
                                      read_only=read_only)
                        self.assertEqual(path.read_bytes(), before)
                        self.assertEqual(path.stat().st_mode, permissions)
                with self.open(path, external_expected_head=anchor, read_only=True) as reader:
                    self.assertEqual(reader.tick, 6)

    def test_hash_valid_history_with_wrong_policy_digest_still_fails(self):
        path = self.root / "forged.sqlite3"
        anchor = self.restore(path, frames=5)
        forged = deepcopy(self.fixture["events"][6]["event"])
        forged["payload"]["developmental"]["policy_sha256"] = "0" * 64
        with LocalNoosphere(path, self.agent_id) as journal:
            anchor = journal.append(forged, expected_head=anchor)
            journal.verify_chain(external_expected_head=anchor, strict_wire=True)
        before = path.read_bytes()
        with self.assertRaisesRegex(AquariumError, "mismatched committed frame"):
            self.open(path, external_expected_head=anchor, read_only=True)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
