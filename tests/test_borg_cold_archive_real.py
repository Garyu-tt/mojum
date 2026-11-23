"""Optional real Borg 1.4.5 end-to-end smoke, run only when binary is installed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.borg_cold_archive import archive_cold_copy, restore_cold_copy


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def _borg_ready():
    binary = shutil.which("borg")
    if not binary:
        return False
    try:
        result = subprocess.run([binary, "--version"], capture_output=True,
                                text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "borg 1.4.5"


@unittest.skipUnless(_borg_ready() and os.name == "posix", "needs Borg 1.4.5 POSIX")
class RealBorgSmoke(unittest.TestCase):
    def test_encrypted_archive_and_restore_with_real_borg_binary(self):
        with tempfile.TemporaryDirectory(prefix="mojum-borg-real-") as temp:
            root = Path(temp)
            repo = root / "repo"
            live = root / "live.sqlite3"
            restored = root / "restored.sqlite3"
            env = os.environ.copy()
            env["BORG_PASSPHRASE"] = "ephemeral-synthetic-ci-fixture-passphrase"
            env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "no"
            with patch.dict(os.environ, env, clear=True):
                subprocess.run(
                    ["borg", "init", "--encryption=repokey-blake2", str(repo)],
                    check=True, capture_output=True, timeout=120,
                )
                with Aquarium(live, SEED, GRAPH) as child:
                    for _ in range(3):
                        child.step(dx=0.02)
                    baseline = child.checkpoint()
                    meta = archive_cold_copy(
                        live, repo, "real-001", SEED, GRAPH,
                        agent_id=SEED["genome_id"],
                    )
                    self.assertEqual(meta["journal_head"], baseline["journal_head"])
                response = restore_cold_copy(
                    repo, "real-001", restored, SEED, GRAPH,
                    agent_id=SEED["genome_id"],
                    expected_head=meta["journal_head"],
                    expected_sha256=meta["db_sha256"],
                    expected_tick=3,
                )
                self.assertEqual(response["db_sha256"], meta["db_sha256"])
                with Aquarium(restored, SEED, GRAPH, read_only=True) as child:
                    self.assertEqual(child.checkpoint(), baseline)


if __name__ == "__main__":
    unittest.main()
