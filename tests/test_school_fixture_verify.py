"""Cold school fixture integrity, privacy and bounded-read regressions."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum import school_verify
from mojum.school import SchoolContractError, load_catalog, run_scripted_activity, write_fixture
from mojum.school_verify import MAX_MANIFEST_BYTES, verify_scripted_fixture

CATALOG_PATH = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class SchoolColdVerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG_PATH)

    def _create(self, parent, section="music", mode="revision", tick=17):
        target = Path(parent) / "private-school"
        run = run_scripted_activity(self.catalog, section, mode=mode, start_tick=tick)
        write_fixture(run, target)
        return target

    def test_both_sections_and_controls_replay_exactly_without_writes(self):
        for section in ("visual_arts", "music"):
            for mode in ("revision", "frozen_control"):
                with self.subTest(section=section, mode=mode), tempfile.TemporaryDirectory() as temp:
                    target = self._create(temp, section, mode, 1234)
                    before = {p.name: p.read_bytes() for p in target.iterdir()}
                    checked = verify_scripted_fixture(target, self.catalog)
                    self.assertEqual(checked["status"], "MATCHES_SCRIPTED_REPLAY")
                    self.assertEqual(checked["file_count"], 3)
                    self.assertEqual(checked["start_tick"], 1234)
                    self.assertEqual(checked["section_id"], section)
                    self.assertEqual(checked["mode"], mode)
                    self.assertEqual(checked["privacy"], "PRIVATE")
                    self.assertFalse(checked["physical_actuation"])
                    self.assertEqual(checked["verified_bytes"], sum(map(len, before.values())))
                    self.assertNotIn("events", checked)
                    self.assertNotIn("artifacts", checked)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in target.iterdir()})

    def test_reject_tampered_music_or_art_without_mutating_it(self):
        for section, file_name in (("music", "revised.json"), ("visual_arts", "baseline.ppm")):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as temp:
                target = self._create(temp, section)
                artifact = target / file_name
                altered = artifact.read_bytes() + b"\n"
                artifact.write_bytes(altered)
                with self.assertRaisesRegex(SchoolContractError, "differs"):
                    verify_scripted_fixture(target, self.catalog)
                self.assertEqual(artifact.read_bytes(), altered)

    def test_wrong_catalog_or_private_to_public_reclassification_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp)
            other = copy.deepcopy(self.catalog)
            other["sections"][0]["title"] += " alternate"
            with self.assertRaisesRegex(SchoolContractError, "manifest differs"):
                verify_scripted_fixture(target, other)

            manifest = target / "events.json"
            raw = manifest.read_bytes()
            self.assertIn(b'"PRIVATE"', raw)
            tampered = raw.replace(b'"PRIVATE"', b'"PUBLIC"', 1)
            manifest.write_bytes(tampered)
            with self.assertRaisesRegex(SchoolContractError, "manifest differs"):
                verify_scripted_fixture(target, self.catalog)

    def test_reject_extra_missing_and_symlink_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp)
            extra = target / "secret.txt"
            extra.write_text("unexpected PRIVATE file", encoding="utf-8")
            with self.assertRaisesRegex(SchoolContractError, "missing or extra"):
                verify_scripted_fixture(target, self.catalog)
            extra.unlink()

            revised = target / "revised.json"
            revised.unlink()
            with self.assertRaisesRegex(SchoolContractError, "missing or extra|cannot read"):
                verify_scripted_fixture(target, self.catalog)
            if os.name == "posix":
                revised.symlink_to("baseline.json")
                with self.assertRaisesRegex(SchoolContractError, "nonregular"):
                    verify_scripted_fixture(target, self.catalog)

    def test_manifest_budget_and_invalid_header_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp)
            path = target / "events.json"
            path.write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
            with self.assertRaisesRegex(SchoolContractError, "byte budget"):
                verify_scripted_fixture(target, self.catalog)
            path.write_text('{"schema":"wrong","events":[]}', encoding="utf-8")
            with self.assertRaises(SchoolContractError):
                verify_scripted_fixture(target, self.catalog)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
        "POSIX directory descriptors required",
    )
    def test_renamed_directory_does_not_redirect_a_pinned_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp, section="music")
            alternate = Path(temp) / "alternate"
            write_fixture(
                run_scripted_activity(self.catalog, "visual_arts", start_tick=55),
                alternate,
            )
            displaced = Path(temp) / "displaced"
            original_read = school_verify._read_regular_bounded
            switched = False

            def swap_before_first_read(path, limit, *, directory_fd=None):
                nonlocal switched
                self.assertIsNotNone(directory_fd)
                if not switched:
                    switched = True
                    target.rename(displaced)
                    target.symlink_to(alternate, target_is_directory=True)
                return original_read(path, limit, directory_fd=directory_fd)

            with patch.object(school_verify, "_read_regular_bounded",
                              side_effect=swap_before_first_read):
                result = verify_scripted_fixture(target, self.catalog)
            self.assertTrue(switched)
            self.assertEqual(result["section_id"], "music")
            self.assertEqual(result["start_tick"], 17)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
        "POSIX nofollow support required",
    )
    def test_symlink_inserted_just_before_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp)
            manifest = target / "events.json"
            outside = Path(temp) / "outside.json"
            original_bytes = manifest.read_bytes()
            outside.write_bytes(original_bytes)
            real_open = os.open
            switched = False

            def swap_during_open(path, flags, *args, **kwargs):
                nonlocal switched
                if path == "events.json" and kwargs.get("dir_fd") is not None and not switched:
                    switched = True
                    manifest.unlink()
                    manifest.symlink_to(outside)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(school_verify.os, "open", side_effect=swap_during_open):
                with self.assertRaisesRegex(SchoolContractError, "nonregular"):
                    verify_scripted_fixture(target, self.catalog)
            self.assertTrue(switched)
            self.assertEqual(outside.read_bytes(), original_bytes)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NONBLOCK"),
        "POSIX nonblocking descriptor opens required",
    )
    def test_fifo_substituted_before_open_fails_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp)
            manifest = target / "events.json"
            real_open = os.open
            swapped = False

            def replace_with_fifo(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == "events.json" and kwargs.get("dir_fd") is not None and not swapped:
                    swapped = True
                    self.assertTrue(flags & os.O_NONBLOCK)
                    manifest.unlink()
                    os.mkfifo(manifest)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(school_verify.os, "open", side_effect=replace_with_fifo):
                with self.assertRaisesRegex(SchoolContractError, "nonregular"):
                    verify_scripted_fixture(target, self.catalog)
            self.assertTrue(swapped)

    def test_cli_reports_only_non_private_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self._create(temp, section="visual_arts", mode="frozen_control")
            env = os.environ.copy()
            src = str(Path(__file__).resolve().parents[1] / "src")
            env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [sys.executable, "-m", "mojum.school_verify", "--catalog", str(CATALOG_PATH),
                 "--fixture", str(target)],
                env=env, capture_output=True, text=True, check=True,
            )
            output = json.loads(proc.stdout)
            self.assertEqual(output["privacy"], "PRIVATE")
            self.assertEqual(output["mode"], "frozen_control")
            self.assertNotIn("anonymous-scripted-fixture", proc.stdout)
            self.assertNotIn("target_beats_ms", proc.stdout)


if __name__ == "__main__":
    unittest.main()
