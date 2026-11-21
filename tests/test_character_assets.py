"""CPU/stdlib-only tests; disposable synthetic glTF fixtures, not Bassy assets."""

import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from mojum.character_assets import (
    CREDIT_TEXT, CREDIT_URL, CharacterAssetError, load_manifest, select_form,
    validate_manifest, verify_files,
)


def glb_fixture():
    data = b'{"asset":{"version":"2.0"},"scenes":[{}]}'
    data += b" " * (-len(data) % 4)
    chunk = struct.pack("<I4s", len(data), b"JSON") + data
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunk)) + chunk


def manifest(blob=None):
    blob = glb_fixture() if blob is None else blob
    return {
        "schema": "mojum.character-assets/1",
        "organism_id": "dante-fixture",
        "origin": "manual-bassy-export",
        "credits": {"text": CREDIT_TEXT, "url": CREDIT_URL},
        "forms": [
            {"age_years": a, "glb_path": f"keyframes/dante-{a}.glb",
             "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
             "height_mm": 1000 + a * 30, "skeleton_version": "rig-r1", "lod": 0}
            for a in (6, 9, 12, 16)
        ],
    }


class CharacterAssetTests(unittest.TestCase):
    def test_select_keyframes_without_mutating_identity_or_input(self):
        original = manifest()
        before = copy.deepcopy(original)
        self.assertEqual([select_form(original, age)["age_years"] for age in (0, 6, 8.9, 9, 11, 12, 16, 100, 10**1000)],
                         [6, 6, 6, 9, 9, 12, 16, 16, 16])
        self.assertEqual(original, before)
        self.assertEqual(validate_manifest(original)["organism_id"], "dante-fixture")
        for value in (True, -1, float("nan"), float("inf"), "16"):
            with self.subTest(value=value), self.assertRaises(CharacterAssetError):
                select_form(original, value)

    def test_canonical_sort_and_independent_copies(self):
        original = manifest()
        original["forms"].reverse()
        validated = validate_manifest(original)
        self.assertEqual([v["age_years"] for v in validated["forms"]], [6, 9, 12, 16])
        validated["forms"][0]["height_mm"] = 2000
        self.assertNotEqual(original["forms"][-1]["height_mm"], 2000)

    def test_fail_closed_on_unsafe_paths_and_attribution(self):
        for path in ("../x.glb", "/tmp/x.glb", "C:/x.glb", "a/../x.glb",
                     "a//x.glb", "a/./x.glb", "a\\x.glb", "a/\x00x.glb", "x.gltf"):
            with self.subTest(path=path):
                candidate = manifest()
                candidate["forms"][0]["glb_path"] = path
                with self.assertRaises(CharacterAssetError):
                    validate_manifest(candidate)
        for txt in ("Bassy", "", "Made with Bassy "):
            candidate = manifest()
            candidate["credits"]["text"] = txt
            with self.assertRaises(CharacterAssetError):
                validate_manifest(candidate)

    def test_reject_duplicate_age_path_invalid_types_and_extra_fields(self):
        mutations = [
            lambda m: m["forms"][1].update(age_years=6),
            lambda m: m["forms"][1].update(glb_path="keyframes/dante-6.glb"),
            lambda m: m["forms"][0].update(age_years=True),
            lambda m: m["forms"][0].update(lod=4),
            lambda m: m["forms"][0].update(bytes=2**63),
            lambda m: m["forms"][0].update(sha256="A" * 64),
            lambda m: m.update(unknown="x"),
            lambda m: m.update(organism_id="unicode-\ud800"),
        ]
        for mutate in mutations:
            candidate = manifest()
            mutate(candidate)
            with self.subTest(candidate=str(candidate)[:80]), self.assertRaises(CharacterAssetError):
                validate_manifest(candidate)

    def test_verify_real_bytes_and_detect_tampering(self):
        blob = glb_fixture()
        data = manifest(blob)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "keyframes").mkdir()
            for form in data["forms"]:
                (root / form["glb_path"]).write_bytes(blob)
            self.assertTrue(verify_files(data, root))
            data_path = root / data["forms"][0]["glb_path"]
            data_path.write_bytes(blob[:-1] + b"x")
            with self.assertRaisesRegex(CharacterAssetError, "digest|JSON"):
                verify_files(data, root)

    def test_verify_rejects_malformed_glb_and_size(self):
        data = manifest()
        data["forms"] = data["forms"][:1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "keyframes").mkdir()
            path = root / data["forms"][0]["glb_path"]
            bad = b"X" + glb_fixture()[1:]
            path.write_bytes(bad)
            data["forms"][0].update(sha256=hashlib.sha256(bad).hexdigest())
            with self.assertRaisesRegex(CharacterAssetError, "header"):
                verify_files(data, root)
            path.write_bytes(bad + b"junk")
            with self.assertRaisesRegex(CharacterAssetError, "size"):
                verify_files(data, root)

    def test_rejects_duplicate_glb_bin_chunks_but_accepts_one(self):
        original = glb_fixture()
        bin_chunk = struct.pack("<I4s", 4, b"BIN\x00") + b"\x00" * 4
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "keyframes").mkdir()
            for count in (1, 2):
                size = len(original) + count * len(bin_chunk)
                candidate = original[:8] + struct.pack("<I", size) + original[12:] + bin_chunk * count
                data = manifest(candidate)
                data["forms"] = data["forms"][:1]
                (root / data["forms"][0]["glb_path"]).write_bytes(candidate)
                if count == 1:
                    self.assertTrue(verify_files(data, root))
                else:
                    with self.assertRaisesRegex(CharacterAssetError, "duplicate GLB BIN"):
                        verify_files(data, root)

    def test_verify_rejects_symlinked_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "keyframes").mkdir()
            blob = glb_fixture()
            real = root / "actual.glb"
            real.write_bytes(blob)
            one = manifest(blob)
            one["forms"] = one["forms"][:1]
            (root / one["forms"][0]["glb_path"]).symlink_to(real)
            with self.assertRaisesRegex(CharacterAssetError, "symlink"):
                verify_files(one, root)

    def test_load_manifest_rejects_duplicate_json_keys_and_oversize(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "character.json"
            data = manifest()
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_manifest(p), validate_manifest(data))
            p.write_text('{"schema":"v1","schema":"v2"}', encoding="utf-8")
            with self.assertRaisesRegex(CharacterAssetError, "duplicate"):
                load_manifest(p)
            p.write_bytes(b" " * (64 * 1024 + 1))
            with self.assertRaisesRegex(CharacterAssetError, "size"):
                load_manifest(p)


if __name__ == "__main__":
    unittest.main()
