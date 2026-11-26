"""Real host restart coverage for redirected legacy console encodings."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, V4_MODE


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class PortableHostEncodingTests(unittest.TestCase):
    def check_start_and_resume(self, encoding, *, motor_mode, sensory_mode,
                               temporal_mode, schema):
        seed = dict(SEED, genome_id="host-\u0416-\u661f-\U0001f98e")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "private.sqlite3"
            genome = root / "seed.json"
            genome.write_text(json.dumps(seed), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                       PYTHONIOENCODING=encoding)
            command = [
                sys.executable, "-m", "mojum.portable_host",
                "--db", str(db), "--genome", str(genome),
                "--interval-ms", "20", "--neural-mode", "disabled",
                "--motor-mode", motor_mode, "--sensory-mode", sensory_mode,
                "--temporal-mode", temporal_mode,
            ]

            def run(ticks, *options):
                process = subprocess.run(
                    [*command, "--ticks", str(ticks), *options],
                    cwd=root, env=env, capture_output=True, timeout=30,
                )
                self.assertEqual(process.returncode, 0,
                                 process.stderr.decode("ascii", errors="backslashreplace"))
                # ASCII JSON is decodable on either redirected legacy stream;
                # escaped Unicode must still recover the exact organism ID.
                status = json.loads(process.stdout.decode("ascii"))
                self.assertEqual(status["agent_id"], seed["genome_id"])
                self.assertEqual(status["schema"], schema)
                self.assertEqual(status["committed_ticks"], ticks)
                return status

            first = run(2)
            self.assertEqual((first["start_tick"], first["end_tick"],
                              first["journal_events"]), (0, 2, 3))
            resumed = run(1, "--expected-head", first["journal_head"])
            self.assertEqual((resumed["start_tick"], resumed["end_tick"],
                              resumed["journal_events"]), (2, 3, 4))
            self.assertNotEqual(resumed["journal_head"], first["journal_head"])

            with Aquarium(
                db, seed, GRAPH, agent_id=seed["genome_id"], neural_mode="disabled",
                motor_mode=motor_mode, sensory_mode=sensory_mode,
                temporal_mode=temporal_mode,
            ) as reopened:
                self.assertEqual((reopened.tick, reopened.store.count()), (3, 4))
                self.assertEqual(reopened.checkpoint()["journal_head"],
                                 resumed["journal_head"])

    def test_legacy_profile_status_survives_restrictive_stdout(self):
        for encoding in ("ascii", "cp1251"):
            with self.subTest(encoding=encoding):
                self.check_start_and_resume(
                    encoding, motor_mode="developmental_fixture",
                    sensory_mode="toy_probe", temporal_mode="range_guided",
                    schema="mojum.portable-host.run/2",
                )

    def test_v4_profile_status_survives_restrictive_stdout(self):
        for encoding in ("ascii", "cp1251"):
            with self.subTest(encoding=encoding):
                self.check_start_and_resume(
                    encoding, motor_mode=V4_MODE, sensory_mode="none",
                    temporal_mode="none", schema="mojum.portable-host.run/3",
                )


if __name__ == "__main__":
    unittest.main()
