"""Read-only v4 wire inspection must enforce the pinned absolute-tick cap.

The bootstrap manifest pins ``max_absolute_ticks`` and the policy selector
already rejects a manifest that rewrites it.  A normally produced journal can
never reach the cap because the 100,000-event ceiling runs out first, so these
tests exercise the frame-level check the same way the existing marker-boundary
test does: a reduced inspector threshold against a real, hash-chained v4
journal.
"""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class V4FrameCapWireTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "v4-cap.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH,
            motor_mode=V4_MODE,
            neural_mode="disabled",
        )

    def test_inspector_accepts_frames_below_the_pinned_cap(self):
        with self.open() as aquarium:
            for _ in range(3):
                aquarium.step()
        with patch("mojum.aquarium_wire.MAX_ABSOLUTE_TICKS", 3):
            result = inspect_journal(self.db)
        self.assertEqual(result["format"], V4_MODE)
        self.assertEqual(result["frames"], 3)

    def test_inspector_rejects_frame_at_the_pinned_cap(self):
        with self.open() as aquarium:
            for _ in range(3):
                aquarium.step()
        with patch("mojum.aquarium_wire.MAX_ABSOLUTE_TICKS", 2):
            with self.assertRaisesRegex(NoosphereError, "pinned absolute-tick cap"):
                inspect_journal(self.db)


if __name__ == "__main__":
    unittest.main()
