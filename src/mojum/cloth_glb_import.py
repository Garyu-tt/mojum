"""Conservative, opt-in GLB2 -> PUBLIC synthetic cloth proxy import.

No runtime physics, optional dependencies, network, canonical Noosphere access or
write access to the origin. The output is geometry only and is not an engine mesh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from typing import Any

SCHEMA = "mojum.cloth.glb-proxy/1"
MAX_FILE = 8 * 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_VERTICES = 4096
MAX_TRIANGLES = 8192
MAX_JOINTS = 128
MAX_ANCHORS = 1024
GLB_HEADER = struct.Struct("<4sII")
CHUNK_HEADER = struct.Struct("<II")
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
SPDX_PUBLIC = frozenset({"CC0-1.0", "CC-BY-4.0", "MIT", "Apache-2.0"})
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
# Untrusted geometry is intentionally limited to an explicitly supported subset.
_COMPONENTS = {5121: ("B", 1), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_DIMS = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


class ClothGLBError(ValueError):
    """A rejected, unsupported, or malformed untrusted GLB asset."""


def _reject(message: str) -> None:
    raise ClothGLBError(message)


def _uint(value: Any, bound: int, name: str) -> int:
    if type(value) is not int or not 0 <= value <= bound:
        _reject(f"invalid {name}")
    return value


def _array(doc: dict[str, Any], key: str, *, max_count: int) -> list[Any]:
    value = doc.get(key)
    if not isinstance(value, list) or len(value) > max_count:
        _reject(f"invalid {key}")
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject(f"invalid {name}")
    return value


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate GLB JSON key")
        result[key] = value
    return result


def _bad_constant(_: str) -> None:
    _reject("non-finite GLB JSON constant")


def _parse_glb(raw: bytes) -> tuple[dict[str, Any], bytes]:
    if not isinstance(raw, bytes) or not GLB_HEADER.size + CHUNK_HEADER.size * 2 < len(raw) <= MAX_FILE:
        _reject("GLB file size outside bounds")
    magic, version, size = GLB_HEADER.unpack_from(raw)
    if magic != b"glTF" or version != 2 or size != len(raw):
        _reject("invalid GLB2 header or total size")
    offset = GLB_HEADER.size
    chunks: list[tuple[int, bytes]] = []
    while offset < len(raw):
        if offset + CHUNK_HEADER.size > len(raw):
            _reject("truncated GLB chunk header")
        size, kind = CHUNK_HEADER.unpack_from(raw, offset)
        offset += CHUNK_HEADER.size
        if size % 4 or size > len(raw) - offset:
            _reject("invalid GLB chunk length")
        chunks.append((kind, raw[offset : offset + size]))
        offset += size
        if len(chunks) > 2:
            _reject("unsupported GLB chunk count")
    if len(chunks) != 2 or [c[0] for c in chunks] != [JSON_CHUNK, BIN_CHUNK]:
        _reject("expected exactly JSON then BIN GLB chunks")
    json_chunk, binary = chunks[0][1], chunks[1][1]
    if len(json_chunk) > MAX_JSON or not json_chunk:
        _reject("GLB JSON chunk size outside bounds")
    try:
        doc = json.loads(
            json_chunk.rstrip(b" ").decode("utf-8"),
            object_pairs_hook=_duplicate_keys,
            parse_constant=_bad_constant,
        )
    except ClothGLBError:
        # Keep deliberate contract failures (e.g. duplicate keys) recognizable.
        raise
    except (ValueError, OverflowError, RecursionError) as exc:
        # json.loads can raise a plain ValueError for >4300-digit JSON ints on
        # Python 3.11+, not just JSONDecodeError. Keep the public API fail-closed.
        raise ClothGLBError("malformed GLB JSON") from exc
    doc = _object(doc, "GLB document")
    asset = _object(doc.get("asset"), "asset")
    if asset.get("version") != "2.0" or asset.get("minVersion") not in (None, "2.0"):
        _reject("unsupported glTF version")
    # Extensions can affect the actual topology/accessor semantics. Fail closed.
    if doc.get("extensionsRequired") or doc.get("extensionsUsed"):
        _reject("GLB extensions are unsupported")
    if doc.get("animations"):
        _reject("animated GLB assets unsupported for deterministic local geometry")
    buffers = _array(doc, "buffers", max_count=1)
    if len(buffers) != 1:
        _reject("exactly one inline GLB BIN buffer required")
    declared = _object(buffers[0], "buffer")
    expected_len = _uint(declared.get("byteLength"), MAX_FILE, "buffer.byteLength")
    if "uri" in declared or len(binary) - expected_len not in (0, 1, 2, 3):
        _reject("external buffer or invalid BIN padding")
    if binary[expected_len:] != b"\x00" * (len(binary) - expected_len):
        _reject("nonzero BIN chunk padding")
    return doc, binary[:expected_len]


def _accessor(
    doc: dict[str, Any], binary: bytes, accessor_index: Any,
    *, semantic: str, count: int | None = None,
) -> list[tuple[int | float, ...]]:
    accessors = _array(doc, "accessors", max_count=64)
    views = _array(doc, "bufferViews", max_count=64)
    index = _uint(accessor_index, len(accessors) - 1, semantic + " accessor index")
    acc = _object(accessors[index], semantic + " accessor")
    view_index = _uint(acc.get("bufferView"), len(views) - 1, "bufferView index")
    view = _object(views[view_index], "bufferView")
    if "sparse" in acc or "extensions" in acc or "extensions" in view:
        _reject("sparse/compressed/extended accessors unsupported")
    if type(view.get("buffer")) is not int or view["buffer"] != 0:
        _reject("accessor uses unsupported buffer")
    kind = acc.get("type")
    typ = acc.get("componentType")
    if kind not in _DIMS or type(typ) is not int or typ not in _COMPONENTS:
        _reject("unsupported accessor component/type")
    formats = {
        "POSITION": ({"VEC3"}, {5126}),
        "JOINTS_0": ({"VEC4"}, {5121, 5123}),
        "WEIGHTS_0": ({"VEC4"}, {5121, 5123, 5126}),
        "indices": ({"SCALAR"}, {5121, 5123, 5125}),
    }
    allowed_types, allowed_components = formats[semantic]
    if kind not in allowed_types or typ not in allowed_components:
        _reject("invalid " + semantic + " accessor format")
    normalized = acc.get("normalized", False)
    if type(normalized) is not bool:
        _reject("invalid accessor normalized flag")
    if normalized != (semantic == "WEIGHTS_0" and typ != 5126):
        _reject("invalid accessor normalization")
    num = _uint(acc.get("count"), MAX_TRIANGLES * 3, "accessor.count")
    if num == 0 or (count is not None and num != count):
        _reject("accessor count mismatch")
    fmt, width = _COMPONENTS[typ]
    elem = width * _DIMS[kind]
    stride = view.get("byteStride", elem)
    if type(stride) is not int or stride < elem or stride > 252 or stride % width:
        _reject("invalid bufferView stride")
    # glTF 2.0 byteStride is defined only for vertex attribute bufferViews.
    # When explicitly present, it must be a multiple of four, including
    # uint8 JOINTS_0 where the component-alignment check alone is too weak.
    if "byteStride" in view:
        if semantic == "indices":
            _reject("indices bufferView must not declare byteStride")
        if stride % 4:
            _reject("attribute byteStride must be a multiple of four")
    base = _uint(view.get("byteOffset", 0), len(binary), "view offset")
    view_len = _uint(view.get("byteLength"), len(binary), "view length")
    local_offset = _uint(acc.get("byteOffset", 0), view_len, "accessor offset")
    if base % width or local_offset % width or base + view_len > len(binary):
        _reject("unaligned or out-of-bounds bufferView")
    if local_offset + (num - 1) * stride + elem > view_len:
        _reject("accessor data outside bufferView")
    unpack = struct.Struct("<" + fmt * _DIMS[kind])
    divisor = (2 ** (8 * width) - 1) if normalized else 1
    rows: list[tuple[int | float, ...]] = []
    for i in range(num):
        values = unpack.unpack_from(binary, base + local_offset + i * stride)
        rows.append(tuple(v / divisor for v in values) if normalized else values)
    return rows


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        _reject("invalid " + label)
    return value


def _joint_names(doc: dict[str, Any]) -> tuple[str, ...]:
    skins = doc.get("skins")
    nodes = doc.get("nodes")
    if not isinstance(skins, list) or len(skins) != 1 or not isinstance(nodes, list) or len(nodes) > 256:
        _reject("skin requires exactly one bounded skin and node table")
    skin = _object(skins[0], "skin")
    joints = skin.get("joints")
    if not isinstance(joints, list) or not 1 <= len(joints) <= MAX_JOINTS:
        _reject("invalid skin joint count")
    names: list[str] = []
    for idx in joints:
        idx = _uint(idx, len(nodes) - 1, "skin joint index")
        name = _identifier(_object(nodes[idx], "joint node").get("name"), "joint name")
        if name in names:
            _reject("duplicate skin joint name")
        names.append(name)
    return tuple(names)


def import_glb(
    raw: bytes,
    *,
    expected_sha256: str,
    asset_id: str,
    license_id: str,
    rig_version: str,
    body_profile: str,
    pinned_indices: tuple[int, ...] = (),
    anchor_joint: str | None = None,
    min_anchor_weight: float = 0.75,
) -> dict[str, Any]:
    """Extract bounded topology and explicitly selected anchors from PUBLIC GLB2.

    No skeletal world-space conversion, mesh optimization or runtime physics is
    claimed; vertices remain in glTF local metres/Y-up coordinates.
    """
    asset_id = _identifier(asset_id, "asset ID")
    rig_version = _identifier(rig_version, "rig version")
    body_profile = _identifier(body_profile, "body profile")
    if type(license_id) is not str or license_id not in SPDX_PUBLIC:
        _reject("unsupported PUBLIC asset license")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        _reject("invalid expected SHA-256")
    if not isinstance(raw, bytes):
        _reject("GLB must be immutable bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        _reject("GLB SHA-256 mismatch")
    if type(min_anchor_weight) is not float or not math.isfinite(min_anchor_weight) or not 0.0 < min_anchor_weight <= 1.0:
        _reject("invalid anchor weight threshold")
    if not isinstance(pinned_indices, tuple) or len(pinned_indices) > MAX_ANCHORS:
        _reject("invalid pinned indices")
    if anchor_joint is not None:
        anchor_joint = _identifier(anchor_joint, "anchor joint")
    if bool(pinned_indices) == bool(anchor_joint):
        _reject("choose exactly one of pinned indices or anchor joint")
    doc, binary = _parse_glb(raw)
    meshes = _array(doc, "meshes", max_count=1)
    if len(meshes) != 1:
        _reject("exactly one mesh required")
    mesh = _object(meshes[0], "mesh")
    primitives = _array(mesh, "primitives", max_count=1)
    if len(primitives) != 1:
        _reject("exactly one mesh primitive required")
    prim = _object(primitives[0], "primitive")
    mode = prim.get("mode", 4)
    if type(mode) is not int or mode != 4 or "targets" in prim or "extensions" in prim:
        _reject("only uncompressed TRIANGLES without morph targets supported")
    attrs = _object(prim.get("attributes"), "primitive attributes")
    if any(k.startswith(("JOINTS_", "WEIGHTS_")) and k not in ("JOINTS_0", "WEIGHTS_0") for k in attrs):
        _reject("secondary joint/weight sets unsupported")
    if "POSITION" not in attrs:
        _reject("POSITION required")
    positions = _accessor(doc, binary, attrs["POSITION"], semantic="POSITION")
    if not 3 <= len(positions) <= MAX_VERTICES:
        _reject("vertex count outside CPU budget")
    vertices: list[list[float]] = []
    for point in positions:
        if any(not math.isfinite(x) or abs(x) > 10 for x in point):
            _reject("invalid or non-finite position")
        vertices.append([float(x) for x in point])
    if "indices" in prim:
        flat = [int(row[0]) for row in _accessor(doc, binary, prim["indices"], semantic="indices")]
    else:
        flat = list(range(len(vertices)))
    if len(flat) % 3 or not 3 <= len(flat) <= MAX_TRIANGLES * 3:
        _reject("triangle count outside CPU budget")
    triangles: list[list[int]] = []
    for i in range(0, len(flat), 3):
        a, b, c = flat[i : i + 3]
        if max(a, b, c) >= len(vertices) or len({a, b, c}) != 3:
            _reject("out-of-range or repeated triangle index")
        av, bv, cv = vertices[a], vertices[b], vertices[c]
        u = [bv[j] - av[j] for j in range(3)]
        v = [cv[j] - av[j] for j in range(3)]
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        if sum(x*x for x in cross) <= 1e-18:
            _reject("zero-area triangle")
        triangles.append([a, b, c])
    skin_present = "JOINTS_0" in attrs or "WEIGHTS_0" in attrs
    if skin_present != ("JOINTS_0" in attrs and "WEIGHTS_0" in attrs):
        _reject("incomplete skin attributes")
    joint_names: tuple[str, ...] = ()
    influences: list[list[list[int | float]]] = []
    if skin_present:
        # A skin list alone does not prove it is bound to this specific mesh.
        nodes = _array(doc, "nodes", max_count=256)
        bindings = [_object(node, "mesh node") for node in nodes
                    if isinstance(node, dict) and type(node.get("mesh")) is int and node["mesh"] == 0]
        if (len(bindings) != 1 or type(bindings[0].get("skin")) is not int
                or bindings[0]["skin"] != 0):
            _reject("skinned mesh must have exactly one mesh-to-skin node binding")
        if any(key in bindings[0] for key in ("matrix", "translation", "rotation", "scale")):
            _reject("mesh node transforms require an upstream bake")
        joint_names = _joint_names(doc)
        joints = _accessor(doc, binary, attrs["JOINTS_0"], semantic="JOINTS_0", count=len(vertices))
        weights = _accessor(doc, binary, attrs["WEIGHTS_0"], semantic="WEIGHTS_0", count=len(vertices))
        for ids, values in zip(joints, weights):
            if any(int(x) >= len(joint_names) for x in ids):
                _reject("joint index outside skin joint table")
            if any(not math.isfinite(w) or w < 0 or w > 1 for w in values):
                _reject("invalid skin weight")
            if abs(sum(values) - 1.0) > 0.02:
                _reject("skin weights do not sum to one")
            influences.append([[int(jid), float(weight)] for jid, weight in zip(ids, values) if weight > 0])
    if anchor_joint is not None:
        if not skin_present or anchor_joint not in joint_names:
            _reject("anchor joint missing from skinned mesh")
        joint_index = joint_names.index(anchor_joint)
        anchors = [i for i, (ids, ws) in enumerate(zip(joints, weights))
                   if sum(float(weight) for jid, weight in zip(ids, ws) if jid == joint_index) >= min_anchor_weight]
    else:
        anchors = sorted(set(_uint(x, len(vertices) - 1, "pinned vertex") for x in pinned_indices))
        if len(anchors) != len(pinned_indices):
            _reject("duplicate pinned vertex")
    if not 1 <= len(anchors) <= MAX_ANCHORS:
        _reject("empty or excessive anchor selection")
    box_min = [min(vertex[j] for vertex in vertices) for j in range(3)]
    box_max = [max(vertex[j] for vertex in vertices) for j in range(3)]
    return {
        "schema": SCHEMA,
        "provenance": {"asset_id": asset_id, "license": license_id, "source_sha256": digest,
                       "classification": "PUBLIC_SYNTHETIC"},
        "geometry": {"unit": "m", "coordinate_system": "gltf2-local-Y-up", "vertices": vertices,
                     "triangles": triangles, "aabb_min": box_min, "aabb_max": box_max},
        "attachment": {"rig_version": rig_version, "body_profile": body_profile,
                       "joint_names": list(joint_names), "anchor_joint": anchor_joint,
                       "skin_influences": influences, "pinned_vertex_indices": anchors},
        "capabilities": {"physical_cloth": False, "simplified_lod": False,
                         "body_collision_proxy": False, "hot_equip": False},
    }


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            _reject("input is not a bounded regular file")
        raw = os.read(fd, MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            _reject("input grew beyond size limit")
        return raw
    finally:
        os.close(fd)


def _write_no_clobber(path: Path, body: bytes) -> None:
    """Stage mode 0600 and link atomically, refusing existing paths/symlinks."""
    if len(body) > MAX_FILE * 3:
        _reject("output size outside bound")
    tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".glb-proxy-", delete=False) as handle:
            tmp = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp, path)
    finally:
        if tmp is not None:
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in, offline PUBLIC synthetic GLB2 cloth proxy importer")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--license", choices=sorted(SPDX_PUBLIC), required=True)
    parser.add_argument("--rig-version", required=True)
    parser.add_argument("--body-profile", required=True)
    anchors = parser.add_mutually_exclusive_group(required=True)
    anchors.add_argument("--pin-index", type=int, action="append")
    anchors.add_argument("--anchor-joint")
    parser.add_argument("--min-anchor-weight", type=float, default=0.75)
    parser.add_argument("--allow-public-synthetic-import", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_public_synthetic_import:
        parser.error("explicit --allow-public-synthetic-import required")
    try:
        raw = _read_regular_file(args.input)
        result = import_glb(
            raw, expected_sha256=args.expected_sha256, asset_id=args.asset_id,
            license_id=args.license, rig_version=args.rig_version,
            body_profile=args.body_profile,
            pinned_indices=tuple(args.pin_index or ()), anchor_joint=args.anchor_joint,
            min_anchor_weight=args.min_anchor_weight,
        )
        output = (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        _write_no_clobber(args.output, output)
    except (OSError, ClothGLBError, OverflowError, struct.error) as exc:
        # Fail closed without putting host paths, private geometry, subprocess output or binary in logs.
        print("cloth GLB import rejected: " + (str(exc) if isinstance(exc, ClothGLBError) else "filesystem/structure error"), file=sys.stderr)
        return 2
    print(json.dumps({"schema": SCHEMA, "sha256": hashlib.sha256(output).hexdigest(),
                      "vertices": len(result["geometry"]["vertices"]),
                      "triangles": len(result["geometry"]["triangles"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
