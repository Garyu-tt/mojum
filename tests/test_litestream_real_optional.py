"""Optional real Litestream 0.5.18 local-file smoke, enabled only by CI/operator.

Set MOJUM_LITESTREAM_BIN to the SHA-pinned trusted native binary and
MOJUM_LITESTREAM_SHA to its independently verified exact bytes.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.litestream_cold_archive import create_local_archive, restore_local_archive

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


@unittest.skipUnless(
    os.name == "posix" and os.environ.get("MOJUM_LITESTREAM_BIN")
    and os.environ.get("MOJUM_LITESTREAM_SHA"),
    "no independently pinned Litestream native binary configured",
)
class NativeLitestreamTests(unittest.TestCase):
    def test_native_one_shot_ltx_restore_replay_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, replica = root / "source.sqlite3", root / "ltx-replica"
            dest = root / "recovered.sqlite3"
            with Aquarium(source, SEED, GRAPH) as aquarium:
                for _ in range(7):
                    aquarium.step(dx=0.01)
                checkpoint = aquarium.checkpoint()
                metadata = create_local_archive(
                    source, replica, SEED, GRAPH,
                    binary=os.environ["MOJUM_LITESTREAM_BIN"],
                    binary_sha256=os.environ["MOJUM_LITESTREAM_SHA"],
                    public_synthetic=True, timeout=120,
                )
                self.assertEqual(metadata["journal_head"], checkpoint["journal_head"])
                self.assertEqual(metadata["event_count"], 8)
                result = restore_local_archive(
                    replica, dest, SEED, GRAPH,
                    binary=os.environ["MOJUM_LITESTREAM_BIN"],
                    binary_sha256=os.environ["MOJUM_LITESTREAM_SHA"],
                    expected_agent_id=metadata["agent_id"],
                    expected_journal_head=metadata["journal_head"],
                    expected_sha256=metadata["restored_sha256"],
                    expected_tick=metadata["tick"],
                    public_synthetic=True, timeout=120,
                )
                self.assertEqual(result["tick"], 7)
                with Aquarium(dest, SEED, GRAPH) as resumed:
                    self.assertEqual(resumed.checkpoint(), checkpoint)
                self.assertEqual(aquarium.checkpoint(), checkpoint)
                with self.assertRaises(FileExistsError):
                    restore_local_archive(
                        replica, dest, SEED, GRAPH,
                        binary=os.environ["MOJUM_LITESTREAM_BIN"],
                        binary_sha256=os.environ["MOJUM_LITESTREAM_SHA"],
                        expected_agent_id=metadata["agent_id"],
                        expected_journal_head=metadata["journal_head"],
                        expected_sha256=metadata["restored_sha256"],
                        expected_tick=metadata["tick"], public_synthetic=True,
                    )


if __name__ == "__main__":
    unittest.main()
