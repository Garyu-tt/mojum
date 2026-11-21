"""Offline Bassy GLB asset manifest contract; not a renderer or biology model.

The organism's identity, memory and GENESIS state never enter this module.
Only manually exported, immutable visual forms are described and verified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

SCHEMA = "mojum.character-assets/1"
CREDIT_TEXT = "Made with Bassy"
CREDIT_URL = "https://bassycharactercreator.itch.io/bassy-character-creator"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_JSON_CHUNK_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 256
MAX_FORMS = 16
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class CharacterAssetError(ValueError):
    """Invalid manifest or untrusted GLB file."""


def _exact_keys(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise CharacterAssetError(f"invalid {label} fields")


def _token(value, label):
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise CharacterAssetError(f"invalid {label}")
    return value


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise CharacterAssetError(f"invalid {label}")
    return value


def _safe_relpath(value):
    if type(value) is not str or not value or len(value) > 240 or "\\" in value or ":" in value:
        raise CharacterAssetError("invalid GLB relative path")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise CharacterAssetError("invalid GLB relative path") from exc
    segments = value.split("/")
    if any(segment in ("", ".", "..") or any(ord(ch) < 32 for ch in segment) for segment in segments):
        raise CharacterAssetError("invalid GLB relative path")
    result = PurePosixPath(value)
    if result.is_absolute() or result.suffix.lower() != ".glb":
        raise CharacterAssetError("invalid GLB relative path")
    return value


def validate_manifest(manifest):
    """Return a detached normalized manifest; perform no file I/O."""
    _exact_keys(manifest, ("schema", "organism_id", "origin", "credits", "forms"), "manifest")
    if manifest["schema"] != SCHEMA or manifest["origin"] != "manual-bassy-export":
        raise CharacterAssetError("unsupported character asset schema or origin")
    _token(manifest["organism_id"], "organism ID")
    _exact_keys(manifest["credits"], ("text", "url"), "credits")
    if manifest["credits"] != {"text": CREDIT_TEXT, "url": CREDIT_URL}:
        raise CharacterAssetError("missing required Bassy attribution")
    forms = manifest["forms"]
    if type(forms) is not list or not 1 <= len(forms) <= MAX_FORMS:
        raise CharacterAssetError("invalid form count")
    ages = set()
    paths = set()
    for form in forms:
        _exact_keys(form, ("age_years", "glb_path", "sha256", "bytes", "height_mm", "skeleton_version", "lod"), "form")
        age = _integer(form["age_years"], 6, 16, "age")
        if age in ages:
            raise CharacterAssetError("duplicate visual age")
        ages.add(age)
        path = _safe_relpath(form["glb_path"])
        if path in paths:
            raise CharacterAssetError("duplicate GLB path")
        paths.add(path)
        if type(form["sha256"]) is not str or not _HEX_SHA256.fullmatch(form["sha256"]):
            raise CharacterAssetError("invalid GLB digest")
        _integer(form["bytes"], 20, MAX_GLB_BYTES, "GLB byte size")
        _integer(form["height_mm"], 300, 2500, "visual height")
        _token(form["skeleton_version"], "skeleton version")
        _integer(form["lod"], 0, 3, "LOD")
    result = copy.deepcopy(manifest)
    result["forms"].sort(key=lambda entry: entry["age_years"])
    return result


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CharacterAssetError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise CharacterAssetError(f"nonfinite JSON value: {value}")


def _check_json_depth(raw):
    """Reject structurally deep JSON before invoking the recursive decoder.

    CPython versions do not expose a portable maximum nesting setting for
    ``json.loads``.  A deeply nested glTF JSON chunk may be accepted by one
    interpreter and raise ``RecursionError`` on another.  This bounded scan
    keeps the asset boundary deterministic and avoids making the decoder do
    unbounded work.  Brackets inside JSON strings are ignored; the decoder
    remains responsible for all other JSON syntax.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:  # quote
            in_string = True
        elif byte in (0x7B, 0x5B):  # { or [
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise CharacterAssetError("JSON nesting depth exceeded")
        elif byte in (0x7D, 0x5D) and depth:
            depth -= 1


def load_manifest(path):
    """Read at most 64 KiB, reject JSON ambiguity, validate offline manifest."""
    path = Path(path)
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise CharacterAssetError("character manifest size exceeded")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CharacterAssetError("character manifest size exceeded")
    try:
        _check_json_depth(raw)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CharacterAssetError("invalid character manifest JSON") from exc
    return validate_manifest(payload)


def select_form(manifest, visual_age_years):
    """Select a clamped whole keyframe; never morph, reset or mutate an agent."""
    manifest = validate_manifest(manifest)
    if type(visual_age_years) is int:
        invalid = visual_age_years < 0
    elif type(visual_age_years) is float:
        invalid = not math.isfinite(visual_age_years) or visual_age_years < 0
    else:
        invalid = True
    if invalid:
        raise CharacterAssetError("invalid visual age")
    forms = manifest["forms"]
    selected = forms[0]
    for form in forms:
        if form["age_years"] <= visual_age_years:
            selected = form
    return copy.deepcopy(selected)


def _verify_glb(stream, size):
    header = stream.read(12)
    if len(header) != 12:
        raise CharacterAssetError("truncated GLB header")
    magic, version, declared_size = struct.unpack("<4sII", header)
    if magic != b"glTF" or version != 2 or declared_size != size:
        raise CharacterAssetError("invalid GLB v2 header/size")
    position = 12
    first = True
    saw_bin = False
    while position < size:
        if size - position < 8:
            raise CharacterAssetError("truncated GLB chunk header")
        chunk_header = stream.read(8)
        if len(chunk_header) != 8:
            raise CharacterAssetError("truncated GLB chunk header")
        chunk_size, chunk_type = struct.unpack("<I4s", chunk_header)
        position += 8
        if chunk_size % 4 or chunk_size > size - position:
            raise CharacterAssetError("invalid GLB chunk size")
        if first:
            if chunk_type != b"JSON" or not 0 < chunk_size <= MAX_JSON_CHUNK_BYTES:
                raise CharacterAssetError("invalid GLB JSON chunk")
            raw_json = stream.read(chunk_size)
            try:
                _check_json_depth(raw_json)
                scene = json.loads(raw_json.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
            except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                raise CharacterAssetError("invalid GLB JSON") from exc
            if type(scene) is not dict or type(scene.get("asset")) is not dict or scene["asset"].get("version") != "2.0":
                raise CharacterAssetError("invalid glTF asset version")
        elif chunk_type != b"BIN\x00":
            raise CharacterAssetError("unexpected GLB chunk")
        elif saw_bin:
            raise CharacterAssetError("duplicate GLB BIN chunk")
        else:
            saw_bin = True
            stream.seek(chunk_size, 1)
        position += chunk_size
        first = False
    if first or position != size:
        raise CharacterAssetError("missing GLB JSON chunk")


def verify_files(manifest, root):
    """Verify all declared local GLB files before use (not a race-safe sandbox)."""
    manifest = validate_manifest(manifest)
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise CharacterAssetError("invalid asset directory")
    for form in manifest["forms"]:
        target = root
        for component in form["glb_path"].split("/"):
            target = target / component
            if target.is_symlink():
                raise CharacterAssetError("symlinked GLB path forbidden")
        try:
            target = target.resolve(strict=True)
            target.relative_to(root)
        except (ValueError, OSError) as exc:
            raise CharacterAssetError("GLB outside asset root or missing") from exc
        if not target.is_file() or target.stat().st_size != form["bytes"]:
            raise CharacterAssetError("GLB size mismatch")
        with target.open("rb") as stream:
            _verify_glb(stream, form["bytes"])
            stream.seek(0)
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: stream.read(65536), b""):
                size += len(block)
                if size > MAX_GLB_BYTES:
                    raise CharacterAssetError("GLB byte budget exceeded")
                digest.update(block)
        if size != form["bytes"] or digest.hexdigest() != form["sha256"]:
            raise CharacterAssetError("GLB digest mismatch")
    return True
