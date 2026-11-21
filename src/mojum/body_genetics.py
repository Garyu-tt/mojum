"""Experimental GENESIS somatic chromosome and Bassy export contracts (CPU-only).

All growth curves below are synthetic placeholders, not human anatomy or
calibrated biomechanics. Bassy is an external, proprietary editor: this module
neither embeds Bassy assets nor calls a nonexistent Bassy API.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

CHROMOSOME_SCHEMA = "mojum.genesis.somatic-chromosome/1"
BODY_SCHEMA = "mojum.genesis.body-phenotype/1"
IDENTITY_SCHEMA = "mojum.agent.identity/1"
ASSET_SCHEMA = "mojum.avatar.bassy-manifest/1"
SELECTION_SCHEMA = "mojum.avatar.selection/1"
AGE_MIN, AGE_MAX = 6, 16
# Multipliers, deliberately independent of names and appearance presets.
SOMATIC_GENES = {
    "height_bias_milli": (800, 1200),
    "head_ratio_bias_milli": (850, 1150),
    "leg_ratio_bias_milli": (850, 1150),
    "shoulder_ratio_bias_milli": (850, 1150),
}
# (age, reference height mm, head:height, leg:height, shoulder:height)
# The last three quantities are fixed-point thousandths.
_SYNTHETIC_GROWTH = (
    (6, 1150, 168, 445, 230),
    (9, 1320, 150, 468, 235),
    (12, 1480, 140, 487, 242),
    (16, 1690, 126, 515, 254),
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class BodyContractError(ValueError):
    """Invalid or unsupported somatic contract."""


def _keys(value, required, path):
    if not isinstance(value, Mapping) or set(value) != set(required):
        raise BodyContractError(f"{path} requires exactly {sorted(required)}")


def _int(value, path, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BodyContractError(f"{path} must be an integer in [{low}, {high}]")
    return value


def _str(value, path, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BodyContractError(f"{path} must be nonempty text (<= {limit} chars)")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
        raise BodyContractError(f"{path} has control/invisible characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise BodyContractError(f"{path} must be valid UTF-8") from exc
    return value


def _digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8", errors="strict")).hexdigest()
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise BodyContractError("Noncanonical UTF-8 JSON") from exc


def _sha(value, path):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BodyContractError(f"{path} must be lowercase sha256 hex")
    return value


def _round_div(num, den):
    """Positive, deterministic half-up integer division."""
    return (num + den // 2) // den


def validate_chromosome(chromosome):
    _keys(chromosome, {"schema", "genome_id", "inherited"}, "chromosome")
    if chromosome["schema"] != CHROMOSOME_SCHEMA:
        raise BodyContractError("Unsupported somatic chromosome schema")
    _str(chromosome["genome_id"], "genome_id")
    _keys(chromosome["inherited"], SOMATIC_GENES, "inherited somatic genes")
    for gene, (lo, hi) in SOMATIC_GENES.items():
        _int(chromosome["inherited"][gene], gene, lo, hi)
    # A JSON round-trip also rejects unrecognized non-JSON values.
    return json.loads(json.dumps(chromosome, ensure_ascii=False, allow_nan=False))


def _growth(age):
    for a, b in zip(_SYNTHETIC_GROWTH, _SYNTHETIC_GROWTH[1:]):
        if a[0] <= age <= b[0]:
            elapsed, interval = age - a[0], b[0] - a[0]
            return tuple(a[i] + _round_div((b[i] - a[i]) * elapsed, interval)
                         for i in range(1, 5))
    raise BodyContractError("Age outside synthetic growth table")


def compile_body(genesis_phenotype, chromosome, age_years):
    """Deterministic GENESIS phenotype + inherited body chromosome -> toy body.

    Age belongs to developmental state, not the inherited genome. The same
    chromosome can produce different age projections without changing identity.
    """
    _int(age_years, "age_years", AGE_MIN, AGE_MAX)
    body_genes = validate_chromosome(chromosome)
    if not isinstance(genesis_phenotype, Mapping):
        raise BodyContractError("GENESIS phenotype is not an object")
    if genesis_phenotype.get("schema") != "mojum.genesis.phenotype/1":
        raise BodyContractError("GENESIS phenotype schema mismatch")
    genome_id = _str(genesis_phenotype.get("genome_id"), "phenotype.genome_id")
    if genome_id != body_genes["genome_id"]:
        raise BodyContractError("Somatic chromosome belongs to another genome")
    tick = _int(genesis_phenotype.get("at_tick"), "at_tick", 0, 2**63 - 1)
    revision = _sha(genesis_phenotype.get("ledger_revision"), "ledger_revision")
    params = genesis_phenotype.get("parameters")
    if not isinstance(params, Mapping):
        raise BodyContractError("Missing GENESIS parameters")
    scale = _int(params.get("body_scale_milli"), "body_scale_milli", 500, 1500)
    ref_height, ref_head, ref_leg, ref_shoulder = _growth(age_years)
    genes = body_genes["inherited"]
    height = _round_div(ref_height * scale * genes["height_bias_milli"], 1_000_000)
    head = _round_div(ref_head * genes["head_ratio_bias_milli"], 1000)
    leg = _round_div(ref_leg * genes["leg_ratio_bias_milli"], 1000)
    shoulder = _round_div(ref_shoulder * genes["shoulder_ratio_bias_milli"], 1000)
    if not (50 <= head < leg < 700 and 100 <= shoulder <= 400):
        raise BodyContractError("Somatic shape ratios violate prototype bounds")
    return {
        "schema": BODY_SCHEMA,
        "genome_id": genome_id,
        "at_tick": tick,
        "age_years": age_years,
        "genesis_revision": revision,
        "somatic_chromosome_sha256": _digest(body_genes),
        "rig_profile": "neutral_humanoid_fixture/1",
        "dimensions": {
            "height_mm": height,
            "head_to_height_milli": head,
            "leg_to_height_milli": leg,
            "shoulder_to_height_milli": shoulder,
        },
        "source_classification": "synthetic_growth_not_biological_anatomy",
    }



def compile_body_at_month(genesis_phenotype, chromosome, age_months):
    """Read-only, integer-only interpolated body projection for ages 6..16.

    The calendar is a separate state, not a chromosome; this interpolates only
    synthetic dimensional keyframes. No topology, rig, animations, skills,
    memories, personality or reproductive anatomy are inferred.
    """
    _int(age_months, "age_months", AGE_MIN * 12, AGE_MAX * 12)
    floor_year, residual = divmod(age_months, 12)
    lower = compile_body(genesis_phenotype, chromosome, floor_year)
    upper = compile_body(genesis_phenotype, chromosome,
                         min(AGE_MAX, floor_year + 1))
    result = dict(lower)
    result["dimensions"] = {
        key: value + _round_div(
            (upper["dimensions"][key] - value) * residual, 12)
        for key, value in lower["dimensions"].items()
    }
    result["age_months"] = age_months
    return result


def validate_human_name(identity, genome_id=None):
    """Names are assigned externally by a human; never encoded in chromosomes."""
    _keys(identity, {"schema", "agent_id", "display_name", "assigned_by"}, "identity")
    if identity["schema"] != IDENTITY_SCHEMA or identity["assigned_by"] != "human":
        raise BodyContractError("Identity requires human assignment contract")
    agent_id = _str(identity["agent_id"], "agent_id")
    _str(identity["display_name"], "display_name", 80)
    if genome_id is not None and agent_id != genome_id:
        raise BodyContractError("Identity belongs to a different genome")
    return dict(identity)


def _asset_path(value):
    path = _str(value, "GLB path", 256)
    parsed = PurePosixPath(path)
    if (path.startswith("/") or "\\" in path or ":" in path or
            any(p in (".", "..", "") for p in path.split("/")) or
            parsed.suffix.lower() != ".glb"):
        raise BodyContractError("GLB path must be a relative safe .glb path")
    return path


def validate_bassy_manifest(manifest):
    """Validate a manifest for manually exported Bassy GLB files, not the files."""
    _keys(manifest, {"schema", "genome_id", "generator", "credits",
                     "stages"}, "avatar manifest")
    if manifest["schema"] != ASSET_SCHEMA or manifest["generator"] != "Bassy":
        raise BodyContractError("Unsupported avatar generator/schema")
    _str(manifest["genome_id"], "manifest.genome_id")
    _keys(manifest["credits"], {"text", "url"}, "credits")
    if (manifest["credits"]["text"] != "Made with Bassy" or
            manifest["credits"]["url"] !=
            "https://bassycharactercreator.itch.io/bassy-character-creator"):
        raise BodyContractError("Required Bassy attribution missing")
    stages = manifest["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= 11:
        raise BodyContractError("Expected 1..11 age-keyframe exports")
    previous = AGE_MIN - 1
    for entry in stages:
        _keys(entry, {"age_years", "path", "sha256", "native_height_mm",
                      "rig_id"}, "asset stage")
        age = _int(entry["age_years"], "asset age", AGE_MIN, AGE_MAX)
        if age <= previous:
            raise BodyContractError("Asset age keyframes must be strictly increasing")
        previous = age
        _asset_path(entry["path"])
        _sha(entry["sha256"], "asset.sha256")
        _int(entry["native_height_mm"], "native_height_mm", 400, 2500)
        _str(entry["rig_id"], "rig_id")
    return json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))


def select_bassy_asset(body, manifest):
    """Choose a verified-by-manifest *reference*, not mesh interpolation."""
    verified = validate_bassy_manifest(manifest)
    if not isinstance(body, Mapping) or body.get("schema") != BODY_SCHEMA:
        raise BodyContractError("Missing compatible body phenotype")
    if body.get("genome_id") != verified["genome_id"]:
        raise BodyContractError("Asset genome mismatch")
    age = _int(body.get("age_years"), "body age", AGE_MIN, AGE_MAX)
    dimensions = body.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise BodyContractError("Missing body dimensions")
    height = _int(dimensions.get("height_mm"), "height_mm", 400, 3500)
    stage = min(verified["stages"], key=lambda item: (abs(item["age_years"] - age),
                                                     item["age_years"]))
    return {
        "schema": SELECTION_SCHEMA,
        "genome_id": body["genome_id"],
        "age_years": age,
        "asset_age_years": stage["age_years"],
        "path": stage["path"],
        "sha256": stage["sha256"],
        "rig_id": stage["rig_id"],
        "scale_milli": _round_div(height * 1000, stage["native_height_mm"]),
        "strategy": "keyframe_glb_no_mesh_morph",
        "credits": dict(verified["credits"]),
        "display_credits": True,
    }


def verify_local_glb(asset_root, selection, max_bytes=128 * 1024 * 1024):
    """Fail closed on paths, symlinks outside root, hash or invalid GLB2 header.

    This is a minimal container/header check, not full glTF validation and not
    a renderer or endorsement of third-party embedded assets.
    """
    if not isinstance(selection, Mapping):
        raise BodyContractError("Missing asset selection")
    name = _asset_path(selection.get("path"))
    expected = _sha(selection.get("sha256"), "selection.sha256")
    root = Path(asset_root).resolve(strict=True)
    path = (root / name).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise BodyContractError("GLB escapes asset root or is not a file")
    size = path.stat().st_size
    if not 12 <= size <= max_bytes:
        raise BodyContractError("Unexpected GLB size")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(12)
        magic, version, declared = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or declared != size:
            raise BodyContractError("Invalid GLB2 header or declared length")
        h.update(header)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        raise BodyContractError("GLB checksum mismatch")
    return True
