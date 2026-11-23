"""Opt-in, stdlib-only garment asset and wardrobe contracts, not cloth physics.

No actual renderer, solver, motor control or Noosphere connection is imported.
Every transition is a detached value with compare-and-swap revision semantics.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct

GARMENT_SCHEMA = "mojum.cloth.garment/1"
WARDROBE_SCHEMA = "mojum.cloth.wardrobe/1"
MAX_MANIFEST_BYTES = 32768
MAX_MESH_BYTES = 32 * 1024 * 1024
MAX_VERTICES = 4096
MAX_ANCHORS = 256
MAX_WORN = 12
_MAX_REVISION = 2**63 - 1
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_PROFILE = re.compile(r"[a-z][a-z0-9_.-]{0,63}/[1-9][0-9]?\Z", re.ASCII)
_DIGEST = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_SLOTS = frozenset({"neck", "torso", "legs", "feet", "hands", "head", "outer"})


class GarmentContractError(ValueError):
    """Invalid or incompatible untrusted garment/wardrobe data."""


def _keys(obj, names, field):
    if type(obj) is not dict or set(obj) != set(names):
        raise GarmentContractError(f"invalid {field} fields")


def _int(value, lower, upper, field):
    if type(value) is not int or not lower <= value <= upper:
        raise GarmentContractError(f"invalid {field}")
    return value


def _token(value, field):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise GarmentContractError(f"invalid {field}")
    return value


def _profile(value):
    if type(value) is not str or _PROFILE.fullmatch(value) is None:
        raise GarmentContractError("invalid body profile")
    return value


def _sha(value):
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise GarmentContractError("invalid mesh SHA-256")
    return value


def _path(value):
    if type(value) is not str or not 1 <= len(value) <= 240 or "\\" in value or ":" in value:
        raise GarmentContractError("invalid mesh path")
    if any(p in ("", ".", "..") or any(ord(ch) < 32 or ord(ch) == 127 for ch in p)
           for p in value.split("/")):
        raise GarmentContractError("invalid mesh path")
    if PurePosixPath(value).is_absolute() or PurePosixPath(value).suffix.lower() != ".glb":
        raise GarmentContractError("mesh must be a relative GLB path")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise GarmentContractError("invalid UTF-8 mesh path") from exc
    return value


def _attribution(value):
    if type(value) is not str or not 1 <= len(value) <= 256 or not value.strip():
        raise GarmentContractError("missing source attribution")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise GarmentContractError("invalid attribution") from exc
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise GarmentContractError("invalid attribution characters")
    return value


def validate_garment(value):
    """Validate a small untrusted declarative manifest; returns a deep copy.

    License metadata is provenance, NOT permission or licensing clearance.
    """
    _keys(value, ("schema", "garment_id", "slot", "source", "mesh", "rig", "material", "anchors"), "garment")
    if type(value["schema"]) is not str or value["schema"] != GARMENT_SCHEMA:
        raise GarmentContractError("unsupported garment schema")
    _token(value["garment_id"], "garment ID")
    if type(value["slot"]) is not str or value["slot"] not in _SLOTS:
        raise GarmentContractError("unsupported wardrobe slot")
    source = value["source"]
    _keys(source, ("origin", "license_spdx", "attribution"), "source")
    if source["origin"] not in ("manual-export", "procedural-synthetic") or type(source["origin"]) is not str:
        raise GarmentContractError("unsupported asset origin")
    _token(source["license_spdx"], "SPDX license identifier")
    _attribution(source["attribution"])
    mesh = value["mesh"]
    _keys(mesh, ("path", "sha256", "bytes", "vertex_count"), "mesh")
    _path(mesh["path"])
    _sha(mesh["sha256"])
    _int(mesh["bytes"], 20, MAX_MESH_BYTES, "mesh size")
    count = _int(mesh["vertex_count"], 1, MAX_VERTICES, "mesh vertex count")
    rig = value["rig"]
    _keys(rig, ("skeleton_version", "body_profile", "age_min", "age_max"), "rig")
    _token(rig["skeleton_version"], "skeleton version")
    _profile(rig["body_profile"])
    age_min = _int(rig["age_min"], 6, 16, "minimum age")
    age_max = _int(rig["age_max"], 6, 16, "maximum age")
    if age_min > age_max:
        raise GarmentContractError("reversed garment age range")
    mat = value["material"]
    _keys(mat, ("mass_g", "thickness_um", "stretch_milli", "bend_milli", "damping_milli"), "material")
    for key, lo, hi in (("mass_g", 1, 5000), ("thickness_um", 100, 20000),
                        ("stretch_milli", 0, 1000), ("bend_milli", 0, 1000),
                        ("damping_milli", 0, 1000)):
        _int(mat[key], lo, hi, key)
    anchors = value["anchors"]
    if type(anchors) is not list or not 1 <= len(anchors) <= MAX_ANCHORS:
        raise GarmentContractError("invalid anchor count")
    seen = set()
    weights: dict[int, int] = {}
    per_vertex: dict[int, int] = {}
    for anchor in anchors:
        _keys(anchor, ("vertex_index", "joint_id", "weight_milli"), "anchor")
        vertex = _int(anchor["vertex_index"], 0, count - 1, "anchor vertex")
        joint = _token(anchor["joint_id"], "anchor joint")
        weight = _int(anchor["weight_milli"], 1, 1000, "anchor weight")
        if (vertex, joint) in seen:
            raise GarmentContractError("duplicate anchor")
        seen.add((vertex, joint))
        weights[vertex] = weights.get(vertex, 0) + weight
        per_vertex[vertex] = per_vertex.get(vertex, 0) + 1
        if weights[vertex] > 1000 or per_vertex[vertex] > 4:
            raise GarmentContractError("unnormalized anchor weights")
    if any(weight != 1000 for weight in weights.values()):
        raise GarmentContractError("unnormalized anchor weights")
    return copy.deepcopy(value)


def _unique_pairs(pairs):
    out = {}
    for key, item in pairs:
        if key in out:
            raise GarmentContractError("duplicate JSON key")
        out[key] = item
    return out


def _reject_constant(value):
    raise GarmentContractError("non-finite JSON value")


def load_garment(path):
    """Bounded JSON manifest loader, no network or executable asset processing."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise GarmentContractError("missing/unreadable garment manifest") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise GarmentContractError("manifest byte budget exceeded")
    try:
        obj = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs,
                         parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise GarmentContractError("invalid garment JSON") from exc
    return validate_garment(obj)


def verify_mesh_file(manifest, root):
    """Verify trusted-size local GLB2 header and declared SHA; no mesh parsing.

    Callers need an immutable asset directory. This is not TOCTOU-safe against
    an attacker actively replacing files while verification runs.
    """
    manifest = validate_garment(manifest)
    try:
        root = Path(root).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise GarmentContractError("missing/invalid asset root") from exc
    if not root.is_dir():
        raise GarmentContractError("asset root is not a directory")
    path = root
    for part in manifest["mesh"]["path"].split("/"):
        path = path / part
        if path.is_symlink():
            raise GarmentContractError("symlinked mesh path")
    try:
        target = path.resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise GarmentContractError("missing/outside-root mesh") from exc
    if not target.is_file() or target.stat().st_size != manifest["mesh"]["bytes"]:
        raise GarmentContractError("mesh size mismatch")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise GarmentContractError("truncated GLB header")
        magic, version, declared = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or declared != manifest["mesh"]["bytes"]:
            raise GarmentContractError("invalid GLB2 header")
        digest.update(header)
        size = 12
        for part in iter(lambda: stream.read(65536), b""):
            size += len(part)
            if size > MAX_MESH_BYTES:
                raise GarmentContractError("mesh byte budget exceeded")
            digest.update(part)
    if size != manifest["mesh"]["bytes"] or digest.hexdigest() != manifest["mesh"]["sha256"]:
        raise GarmentContractError("mesh SHA-256 mismatch")
    return True


def new_wardrobe(skeleton_version, body_profile, age_years):
    """Create a pure-data wardrobe detached from organism identity/memory."""
    return {"schema": WARDROBE_SCHEMA, "revision": 0, "rig": {
        "skeleton_version": _token(skeleton_version, "skeleton version"),
        "body_profile": _profile(body_profile),
        "age_years": _int(age_years, 6, 16, "body age")}, "worn": []}


def validate_wardrobe(state):
    _keys(state, ("schema", "revision", "rig", "worn"), "wardrobe")
    if type(state["schema"]) is not str or state["schema"] != WARDROBE_SCHEMA:
        raise GarmentContractError("unsupported wardrobe schema")
    _int(state["revision"], 0, _MAX_REVISION, "wardrobe revision")
    _keys(state["rig"], ("skeleton_version", "body_profile", "age_years"), "wardrobe rig")
    _token(state["rig"]["skeleton_version"], "skeleton version")
    _profile(state["rig"]["body_profile"])
    _int(state["rig"]["age_years"], 6, 16, "body age")
    worn = state["worn"]
    if type(worn) is not list or len(worn) > MAX_WORN:
        raise GarmentContractError("invalid wardrobe size")
    ids, slots = set(), set()
    for item in worn:
        _keys(item, ("garment_id", "slot", "sha256"), "worn garment")
        _token(item["garment_id"], "garment ID")
        if type(item["slot"]) is not str or item["slot"] not in _SLOTS:
            raise GarmentContractError("invalid worn slot")
        _sha(item["sha256"])
        if item["garment_id"] in ids or item["slot"] in slots:
            raise GarmentContractError("duplicate worn garment or slot")
        ids.add(item["garment_id"])
        slots.add(item["slot"])
    return copy.deepcopy(state)


def _revision(state, expected_revision):
    _int(expected_revision, 0, _MAX_REVISION, "expected revision")
    if state["revision"] != expected_revision:
        raise GarmentContractError("stale wardrobe revision")
    if expected_revision == _MAX_REVISION:
        raise GarmentContractError("wardrobe revision exhausted")


def _compatible(manifest, rig):
    spec = manifest["rig"]
    if (spec["skeleton_version"] != rig["skeleton_version"] or
            spec["body_profile"] != rig["body_profile"] or
            not spec["age_min"] <= rig["age_years"] <= spec["age_max"]):
        raise GarmentContractError("garment incompatible with rig/profile/age")


def equip(state, manifest, *, expected_revision):
    """Pure CAS transition, with no solver calls and no implicit auto-unequip."""
    state = validate_wardrobe(state)
    manifest = validate_garment(manifest)
    _revision(state, expected_revision)
    _compatible(manifest, state["rig"])
    if len(state["worn"]) == MAX_WORN or any(
            item["garment_id"] == manifest["garment_id"] or item["slot"] == manifest["slot"]
            for item in state["worn"]):
        raise GarmentContractError("garment or wardrobe slot already occupied")
    state["worn"].append({"garment_id": manifest["garment_id"],
                          "slot": manifest["slot"], "sha256": manifest["mesh"]["sha256"]})
    state["worn"].sort(key=lambda item: item["slot"])
    state["revision"] += 1
    return state


def unequip(state, garment_id, *, expected_revision):
    state = validate_wardrobe(state)
    _token(garment_id, "garment ID")
    _revision(state, expected_revision)
    remain = [item for item in state["worn"] if item["garment_id"] != garment_id]
    if len(remain) == len(state["worn"]):
        raise GarmentContractError("garment is not worn")
    state["worn"] = remain
    state["revision"] += 1
    return state


def rebind_wardrobe(state, skeleton_version, body_profile, age_years, manifests, *, expected_revision):
    """Fail closed on age/rig changes; never silently remove clothing.

    Every worn item must have a supplied matching manifest at the same digest.
    Callers provide an immutable manifest registry; no lookup/network here.
    """
    state = validate_wardrobe(state)
    _revision(state, expected_revision)
    next_rig = {"skeleton_version": _token(skeleton_version, "skeleton version"),
                "body_profile": _profile(body_profile),
                "age_years": _int(age_years, 6, 16, "body age")}
    if type(manifests) is not dict or len(manifests) > MAX_WORN:
        raise GarmentContractError("invalid manifest registry")
    if set(manifests) != {item["garment_id"] for item in state["worn"]}:
        raise GarmentContractError("manifest registry must match worn garments")
    for worn in state["worn"]:
        garment = validate_garment(manifests[worn["garment_id"]])
        if (garment["garment_id"] != worn["garment_id"] or
                garment["slot"] != worn["slot"] or garment["mesh"]["sha256"] != worn["sha256"]):
            raise GarmentContractError("worn garment manifest mismatch")
        _compatible(garment, next_rig)
    state["rig"] = next_rig
    state["revision"] += 1
    return state
