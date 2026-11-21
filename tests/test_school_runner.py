"""Executable, bounded scripted school tests (not learner-quality tests)."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.school import (
    MAX_CATALOG_BYTES,
    SchoolContractError,
    load_catalog,
    run_scripted_activity,
    validate_catalog,
    write_fixture,
)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class SchoolRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_art_two_revisions_and_observable_feedback(self):
        run = run_scripted_activity(self.catalog, "visual_arts", start_tick=12)
        self.assertEqual(run["feedback"]["metric"], "mirror_equal_pairs_of_288")
        self.assertGreater(run["feedback"]["revised"], run["feedback"]["baseline"])
        self.assertEqual(list(run["artifacts"]), ["baseline.ppm", "revised.ppm"])
        self.assertNotEqual(*run["artifacts"].values())
        for content in run["artifacts"].values():
            self.assertTrue(content.startswith(b"P3\n"))
            self.assertLessEqual(len(content), 32768)
        self.assertEqual([e["sim_tick"] for e in run["events"]], [12, 13, 14, 15, 16])

    def test_music_two_revisions_and_timing_error(self):
        run = run_scripted_activity(self.catalog, "music")
        self.assertEqual(run["feedback"]["baseline"], 200)
        self.assertEqual(run["feedback"]["revised"], 0)
        timing = json.loads(run["artifacts"]["revised.json"])
        self.assertEqual(timing["played_beats_ms"], timing["target_beats_ms"])
        self.assertEqual(timing["origin"], "SCRIPTED_FIXTURE_ONLY")

    def test_frozen_control_independent_and_no_improvement(self):
        for section in ("music", "visual_arts"):
            with self.subTest(section=section):
                control = run_scripted_activity(self.catalog, section, mode="frozen_control")
                revised = run_scripted_activity(self.catalog, section, mode="revision")
                self.assertEqual(control["feedback"]["delta"], 0)
                names = list(control["artifacts"])
                self.assertEqual(control["artifacts"][names[0]], control["artifacts"][names[1]])
                self.assertNotEqual(control["events"], revised["events"])

    def test_replay_content_and_hash_linkage(self):
        for section in ("music", "visual_arts"):
            first = run_scripted_activity(self.catalog, section, start_tick=123)
            second = run_scripted_activity(self.catalog, section, start_tick=123)
            self.assertEqual(first, second)
            self.assertEqual([event["phase"] for event in first["events"]],
                             ["observe", "experiment", "make", "revise", "reflect"])
            for event in first["events"]:
                self.assertEqual(event["visibility"], "PRIVATE")
                self.assertFalse(event["provenance"]["physical_actuation"])
                self.assertEqual(event["learner_ref"], "anonymous-scripted-fixture")
            for event in first["events"][2:4]:
                meta = event["evidence"]["artifact"]
                content = first["artifacts"][meta["filename"]]
                self.assertEqual(meta["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(meta["size_bytes"], len(content))

    def test_invalid_tick_mode_or_section_rejected(self):
        for tick in (True, -1, 2**63 - 4, 1.5, "5"):
            with self.subTest(tick=tick), self.assertRaises(SchoolContractError):
                run_scripted_activity(self.catalog, "music", start_tick=tick)
        for section in ("dance", "bogus", [], {}, True, None):
            with self.subTest(section=section), self.assertRaises(SchoolContractError):
                run_scripted_activity(self.catalog, section)
        with self.assertRaises(SchoolContractError):
            run_scripted_activity(self.catalog, "music", mode="unknown")

    def test_invalid_schema_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "catalog.json"
            for content in (b'{"schema":"a","schema":"b"}',
                            b'{"schema":"mojum.school-catalog/99"}',
                            b'{not-json}', b'\xff', b'x' * 65_537):
                path.write_bytes(content)
                with self.subTest(content=content[:20]), self.assertRaises(SchoolContractError):
                    load_catalog(path)

    def test_catalog_file_rejects_nonfinite_numbers_and_invalid_unicode(self):
        catalog_json = json.dumps(self.catalog)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "catalog.json"
            for value in ("NaN", "Infinity", "-Infinity", "1e999", r'"\ud800"'):
                path.write_text(catalog_json[:-1] + ', "metadata": ' + value + "}",
                                encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(SchoolContractError):
                    load_catalog(path)

    def test_in_memory_catalog_uses_the_same_json_and_size_contract(self):
        values = {
            "nonfinite": float("nan"),
            "invalid-unicode": "\ud800",
            "non-json": object(),
            "oversized": "a" * MAX_CATALOG_BYTES,
        }
        for label, value in values.items():
            catalog = {**self.catalog, "metadata": value}
            with self.subTest(label=label):
                with self.assertRaises(SchoolContractError):
                    validate_catalog(catalog)
                with self.assertRaises(SchoolContractError):
                    run_scripted_activity(catalog, "music")

    def test_canonical_json_exact_byte_limit(self):
        def canonical_bytes(value):
            return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

        catalog = {**self.catalog, "metadata": ""}
        filler = MAX_CATALOG_BYTES + 1 - len(canonical_bytes(catalog))
        self.assertGreater(filler, 0)
        catalog["metadata"] = "x" * filler
        self.assertEqual(len(canonical_bytes(catalog)), MAX_CATALOG_BYTES + 1)
        with self.assertRaises(SchoolContractError):
            validate_catalog(catalog)
        catalog["metadata"] = catalog["metadata"][:-1]
        self.assertEqual(len(canonical_bytes(catalog)), MAX_CATALOG_BYTES)
        self.assertIs(validate_catalog(catalog), catalog)

    def test_malformed_structure_and_resource_budgets(self):
        import copy
        edits = []
        c = copy.deepcopy(self.catalog); c["sections"] = c["sections"] * 3; edits.append(c)
        c = copy.deepcopy(self.catalog); c["sections"][0]["cross_subject_ids"] = ["missing"]; edits.append(c)
        c = copy.deepcopy(self.catalog); c["sections"][0]["id"] = []; edits.append(c)
        c = copy.deepcopy(self.catalog); c["learning_loop"] = ["reflect"]; edits.append(c)
        c = copy.deepcopy(self.catalog); c["sections"][0]["starter_activity"]["id"] = "evil"; edits.append(c)
        for bad in edits:
            with self.subTest(bad=str(bad)[:80]), self.assertRaises(SchoolContractError):
                run_scripted_activity(bad, "music")

    def test_write_private_and_no_clobber(self):
        run = run_scripted_activity(self.catalog, "music")
        with tempfile.TemporaryDirectory() as dirname:
            dest = Path(dirname) / "output"
            write_fixture(run, dest)
            manifest = json.loads((dest / "events.json").read_bytes())
            self.assertEqual(manifest["schema"], "mojum.school-scripted-run/1")
            self.assertEqual({event["schema"] for event in manifest["events"]},
                             {"mojum.school-scripted-event/1"})
            self.assertNotIn("artifacts", manifest)
            self.assertEqual(manifest["visibility"], "PRIVATE")
            self.assertEqual(sorted(p.name for p in dest.iterdir()),
                             ["baseline.json", "events.json", "revised.json"])
            with self.assertRaises(FileExistsError):
                write_fixture(run, dest)
            if sys.platform != "win32":
                self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in dest.iterdir()))

    def test_real_cli_subprocess(self):
        with tempfile.TemporaryDirectory() as dirname:
            dest = Path(dirname) / "school-output"
            completed = subprocess.run(
                [sys.executable, "-m", "mojum.school", "--catalog", str(CATALOG_PATH),
                 "--section", "visual_arts", "--out", str(dest), "--start-tick", "80"],
                capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(completed.stdout)["phase_count"], 5)
            self.assertEqual(json.loads((dest / "events.json").read_text())["events"][0]["sim_tick"], 80)
            again = subprocess.run(
                [sys.executable, "-m", "mojum.school", "--catalog", str(CATALOG_PATH),
                 "--section", "visual_arts", "--out", str(dest)], capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("exists", again.stderr)


if __name__ == "__main__":
    unittest.main()
