"""Offline synthetic-only GLB fixtures; no downloaded models or dependencies."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest

from mojum.cloth_glb_import import (
    MAX_FILE, ClothGLBError, _parse_glb, import_glb, main,
)


def fixture(*, skin=False, bad_index=False, zero_area=False, external=False,
            extensions=False, invalid_weight=False, secondary=False,
            nonfinite=False, interleaved=False, nodes_missing=False):
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 0}],
           "bufferViews": [], "accessors": [], "meshes": [{"name": "test_cloth", "primitives": [{}]}]}
    binary = bytearray()

    def accessor(items, fmt, kind, *, normalized=False, stride=None):
        pos = len(binary)
        while len(binary) % 4:
            binary.append(0)
        pos = len(binary)
        num = len(items[0])
        width = struct.calcsize("<" + fmt)
        packed = width * num
        step = stride or packed
        for item in items:
            binary.extend(struct.pack("<" + fmt * num, *item))
            if step > packed:
                binary.extend(b"\x00" * (step - packed))
        view = {"buffer": 0, "byteOffset": pos, "byteLength": step * len(items)}
        if stride:
            view["byteStride"] = stride
        view_idx = len(doc["bufferViews"])
        doc["bufferViews"].append(view)
        comp = {"f": 5126, "H": 5123, "B": 5121, "I": 5125}[fmt]
        acc = {"bufferView": view_idx, "byteOffset": 0, "componentType": comp,
               "count": len(items), "type": kind}
        if normalized:
            acc["normalized"] = True
        idx = len(doc["accessors"])
        doc["accessors"].append(acc)
        return idx

    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (1., 1., 0.)]
    if zero_area:
        positions[-1] = (0.5, 0.5, 0.)
    if nonfinite:
        positions[-1] = (float("nan"), 1., 0.)
    attrs = {"POSITION": accessor(positions, "f", "VEC3", stride=16 if interleaved else None)}
    triangles = [(0,), (1,), (2,), (1,), (3,), ((4 if bad_index else 2),)]
    prim = doc["meshes"][0]["primitives"][0]
    prim["attributes"] = attrs
    prim["indices"] = accessor(triangles, "H", "SCALAR")
    if skin:
        attrs["JOINTS_0"] = accessor([(0, 1, 0, 0)] * 4, "B", "VEC4")
        value = 100 if invalid_weight else 255
        attrs["WEIGHTS_0"] = accessor([(value, 0, 0, 0)] * 4, "B", "VEC4", normalized=True)
        doc["nodes"] = [{"name": "neck"}, {"name": "hand"}, {"mesh": 0, "skin": 0}]
        if nodes_missing:
            del doc["nodes"]
        doc["skins"] = [{"joints": [0, 1]}]
    if secondary:
        doc["meshes"].append({"primitives": [prim]})
    if external:
        doc["buffers"][0]["uri"] = "https://host.invalid/private.bin"
    if extensions:
        doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    doc["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary.extend(b"\x00" * (-len(binary) % 4))
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return struct.pack("<4sII", b"glTF", 2, length) + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + struct.pack("<II", len(binary), 0x004E4942) + bytes(binary)


def convert(raw, **kwargs):
    defaults = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), asset_id="public_scarf",
                    license_id="CC0-1.0", rig_version="dante-rig-r01",
                    body_profile="adult", pinned_indices=(0, 1))
    defaults.update(kwargs)
    return import_glb(raw, **defaults)


class ClothGLBTests(unittest.TestCase):
    def test_valid_synthetic_fixed_layout_and_repeatable_result(self):
        raw = fixture()
        result = convert(raw)
        self.assertEqual(result["schema"], "mojum.cloth.glb-proxy/1")
        self.assertEqual(result["geometry"]["triangles"], [[0, 1, 2], [1, 3, 2]])
        self.assertEqual(result["attachment"]["pinned_vertex_indices"], [0, 1])
        self.assertEqual(result["geometry"]["coordinate_system"], "gltf2-local-Y-up")
        self.assertFalse(result["capabilities"]["physical_cloth"])
        self.assertEqual(result, convert(raw))

    def test_interleaved_positions_and_unsigned_short_indices(self):
        self.assertEqual(len(convert(fixture(interleaved=True))["geometry"]["vertices"]), 4)

    def test_skin_anchor_selection_by_joint_weight(self):
        raw = fixture(skin=True)
        out = convert(raw, pinned_indices=(), anchor_joint="neck")
        self.assertEqual(out["attachment"]["joint_names"], ["neck", "hand"])
        self.assertEqual(out["attachment"]["pinned_vertex_indices"], [0, 1, 2, 3])
        self.assertEqual(out["attachment"]["skin_influences"][0], [[0, 1.0]])

    def test_skin_missing_joint_and_bad_weights_fail(self):
        with self.assertRaisesRegex(ClothGLBError, "anchor joint missing"):
            convert(fixture(skin=True), pinned_indices=(), anchor_joint="spine")
        with self.assertRaisesRegex(ClothGLBError, "weights do not sum"):
            convert(fixture(skin=True, invalid_weight=True))
        with self.assertRaisesRegex(ClothGLBError, "nodes"):
            convert(fixture(skin=True, nodes_missing=True))

    def test_digest_binding_is_mandatory(self):
        with self.assertRaisesRegex(ClothGLBError, "SHA-256 mismatch"):
            convert(fixture(), expected_sha256="0" * 64)
        with self.assertRaises(ClothGLBError):
            convert(fixture(), expected_sha256="abc")

    def test_public_license_and_id_allowlist(self):
        with self.assertRaisesRegex(ClothGLBError, "license"):
            convert(fixture(), license_id="Unlicensed")
        with self.assertRaises(ClothGLBError):
            convert(fixture(), asset_id="../private")

    def test_glb_header_and_chunk_rejections(self):
        raw = fixture()
        for modified in [b"", b"\0" + raw[1:], raw[:4] + struct.pack("<I", 1) + raw[8:],
                         raw[:8] + struct.pack("<I", len(raw) + 4) + raw[12:], raw[:-1]]:
            with self.subTest(length=len(modified)):
                with self.assertRaises(ClothGLBError):
                    convert(modified)

    def test_external_uri_and_required_extensions_rejected(self):
        with self.assertRaisesRegex(ClothGLBError, "external"):
            convert(fixture(external=True))
        with self.assertRaisesRegex(ClothGLBError, "extensions"):
            convert(fixture(extensions=True))

    def test_out_of_bounds_index_rejected(self):
        with self.assertRaisesRegex(ClothGLBError, "out-of-range"):
            convert(fixture(bad_index=True))

    def test_ambiguous_mesh_rejected(self):
        with self.assertRaisesRegex(ClothGLBError, "meshes"):
            convert(fixture(secondary=True))

    def test_degenerate_triangle_rejected(self):
        with self.assertRaisesRegex(ClothGLBError, "zero-area"):
            convert(fixture(zero_area=True))

    def test_nonfinite_vertices_rejected(self):
        with self.assertRaisesRegex(ClothGLBError, "non-finite"):
            convert(fixture(nonfinite=True))

    def test_duplicate_anchor_and_empty_anchor_fail(self):
        with self.assertRaisesRegex(ClothGLBError, "duplicate pinned"):
            convert(fixture(), pinned_indices=(0, 0))
        with self.assertRaisesRegex(ClothGLBError, "choose exactly one"):
            convert(fixture(), pinned_indices=())

    def test_json_duplicate_keys_rejected(self):
        raw = fixture()
        doc, blob = _parse_glb(raw)
        encoded = json.dumps(doc, separators=(",", ":")).encode()
        encoded = b'{"asset":{"version":"2.0"},"asset":{"version":"2.0"},' + encoded[1:]
        encoded += b" " * (-len(encoded) % 4)
        blob += b"\x00" * (-len(blob) % 4)
        length = 12 + 8 + len(encoded) + 8 + len(blob)
        modified = (struct.pack("<4sII", b"glTF", 2, length) +
                    struct.pack("<II", len(encoded), 0x4E4F534A) + encoded +
                    struct.pack("<II", len(blob), 0x004E4942) + blob)
        with self.assertRaisesRegex(ClothGLBError, "duplicate"):
            convert(modified)

    def test_accessor_byte_length_outside_view(self):
        raw = fixture()
        doc, blob = _parse_glb(raw)
        doc["bufferViews"][0]["byteLength"] = 4
        encoded = json.dumps(doc, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        blob += b"\x00" * (-len(blob) % 4)
        length = 12 + 8 + len(encoded) + 8 + len(blob)
        changed = (struct.pack("<4sII", b"glTF", 2, length) +
                   struct.pack("<II", len(encoded), 0x4E4F534A) + encoded +
                   struct.pack("<II", len(blob), 0x004E4942) + blob)
        with self.assertRaisesRegex(ClothGLBError, "outside bufferView"):
            convert(changed)

    def test_reject_excessive_input_size_before_parser(self):
        with self.assertRaises(ClothGLBError):
            convert(b"x" * (MAX_FILE + 1))

    def test_cli_explicit_opt_in_and_no_clobber_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "public.glb"
            output = Path(directory) / "mesh.json"
            raw = fixture()
            source.write_bytes(raw)
            argv = ["--input", str(source), "--output", str(output),
                    "--expected-sha256", hashlib.sha256(raw).hexdigest(),
                    "--asset-id", "scarf", "--license", "CC0-1.0",
                    "--rig-version", "rig-1", "--body-profile", "public-test",
                    "--pin-index", "0", "--pin-index", "1"]
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(argv)
            self.assertFalse(output.exists())
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(argv + ["--allow-public-synthetic-import"]), 0)
            self.assertEqual(json.loads(printed.getvalue())["vertices"], 4)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            old = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv + ["--allow-public-synthetic-import"]), 2)
            self.assertEqual(old, output.read_bytes())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_cli_refuses_symlink_source_and_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "public.glb"
            target = Path(directory) / "source_link.glb"
            output = Path(directory) / "mesh.json"
            raw = fixture()
            source.write_bytes(raw)
            target.symlink_to(source)
            argv = ["--input", str(target), "--output", str(output),
                    "--expected-sha256", hashlib.sha256(raw).hexdigest(),
                    "--asset-id", "scarf", "--license", "CC0-1.0", "--rig-version", "rig-1",
                    "--body-profile", "test", "--pin-index", "0", "--allow-public-synthetic-import"]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 2)
            self.assertFalse(output.exists())
            output.symlink_to(source)
            argv[1] = str(source)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 2)
            self.assertEqual(source.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
