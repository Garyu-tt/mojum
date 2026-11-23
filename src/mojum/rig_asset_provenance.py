"""Offline virtual rig provenance declarations and binary integrity checks.

This never authenticates publishers or licenses and never authorizes motion.
No files are opened, no network is used, and no MuJoCo state is changed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

SCHEMA = "mojum.virtual-rig-provenance/1"
RECEIPT_SCHEMA = "mojum.virtual-rig-integrity-receipt/1"
JOINT_COUNT = 53
DOF_COUNT = 85
MAX_ASSETS = 16
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_LICENSE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{0,63}\Z", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REV = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_FILENAME = re.compile(r"assets/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z", re.ASCII)


class RigProvenanceError(ValueError):
    """A manifest or supplied in-memory asset fails admission."""


def _keys(obj: object, expected: set[str], label: str) -> dict:
    if (type(obj) is not dict or len(obj) != len(expected)
            or any(type(key) is not str for key in obj)
            or set(obj) != expected):
        raise RigProvenanceError(f"invalid {label} fields")
    return obj


def _valid_name(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _canonical(obj: object) -> bytes:
    try:
        return json.dumps(
            obj, sort_keys=True, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise RigProvenanceError("noncanonical rig manifest") from exc


def verify_rig_assets(manifest: object, contents: object) -> dict:
    """Verify a bounded synthetic 53-joint/85-DOF asset bundle.

    Contents is an exact dict of immutable bytes, indexed by declared paths.
    Receipt digests are reproducible integrity checks, not signatures.
    """
    m = _keys(manifest, {
        "schema", "classification", "source", "joints", "assets",
        "calibration_evidence_asset",
    }, "manifest")
    # Reject hostile equality hooks before comparing the version selector.
    if type(m["schema"]) is not str or m["schema"] != SCHEMA:
        raise RigProvenanceError("unsupported rig provenance schema")
    if type(m["classification"]) is not str or m["classification"] not in {
        "synthetic_unverified", "calibration_claim_unverified",
    }:
        raise RigProvenanceError("invalid provenance classification")
    source = _keys(m["source"], {"repository", "revision", "license_spdx"}, "source")
    repo = source["repository"]
    if (type(repo) is not str or repo.count("/") != 1
            or any(not _valid_name(part, _SLUG) or ".." in part for part in repo.split("/"))):
        raise RigProvenanceError("invalid repository declaration")
    if not _valid_name(source["revision"], _REV):
        raise RigProvenanceError("source revision must be a pinned 40-hex commit")
    if not _valid_name(source["license_spdx"], _LICENSE):
        raise RigProvenanceError("invalid license identifier declaration")

    joints = m["joints"]
    if type(joints) is not list or len(joints) != JOINT_COUNT:
        raise RigProvenanceError("expected 53 ordered joints")
    seen: set[str] = set()
    total_dof = 0
    roots = 0
    for joint in joints:
        j = _keys(joint, {"id", "parent", "dof"}, "joint")
        jid, parent, dof = j["id"], j["parent"], j["dof"]
        if not _valid_name(jid, _ID) or jid in seen:
            raise RigProvenanceError("invalid or duplicate joint id")
        if parent is None:
            roots += 1
            if seen:
                raise RigProvenanceError("rig root must be first")
        elif type(parent) is not str or parent not in seen:
            raise RigProvenanceError("joint parent must precede child")
        if type(dof) is not int or not 1 <= dof <= 6:
            raise RigProvenanceError("invalid DOF count")
        seen.add(jid)
        total_dof += dof
    if roots != 1 or total_dof != DOF_COUNT:
        raise RigProvenanceError("expected one root and 85 total DOFs")

    assets = m["assets"]
    if type(assets) is not list or not 1 <= len(assets) <= MAX_ASSETS:
        raise RigProvenanceError("invalid asset count")
    names: set[str] = set()
    declared_total = 0
    for asset in assets:
        a = _keys(asset, {"path", "bytes", "sha256"}, "asset")
        path, size = a["path"], a["bytes"]
        if (not _valid_name(path, _FILENAME) or ".." in path
                or path in names):
            raise RigProvenanceError("unsafe or duplicate asset path")
        if type(size) is not int or not 0 <= size <= MAX_ASSET_BYTES:
            raise RigProvenanceError("invalid asset size")
        if not _valid_name(a["sha256"], _HEX):
            raise RigProvenanceError("invalid asset digest")
        names.add(path)
        declared_total += size
        if declared_total > MAX_TOTAL_BYTES:
            raise RigProvenanceError("asset bundle budget exceeded")
    evidence = m["calibration_evidence_asset"]
    if m["classification"] == "synthetic_unverified":
        if evidence is not None:
            raise RigProvenanceError("synthetic rig cannot declare calibration evidence")
    elif type(evidence) is not str or evidence not in names:
        raise RigProvenanceError("calibration claim must reference supplied evidence")

    if (type(contents) is not dict or len(contents) != len(names)
            or any(type(key) is not str for key in contents)
            or set(contents) != names):
        raise RigProvenanceError("missing or unexpected asset bytes")
    total_bytes = 0
    for a in assets:
        raw = contents[a["path"]]
        if type(raw) is not bytes or len(raw) != a["bytes"]:
            raise RigProvenanceError("asset content type or byte length differs")
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            raise RigProvenanceError("asset bytes exceed total budget")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), a["sha256"]):
            raise RigProvenanceError("asset digest mismatch")

    # A receipt does not include arbitrary source strings or binary payloads.
    return {
        "schema": RECEIPT_SCHEMA,
        "classification": m["classification"],
        "manifest_sha256": hashlib.sha256(_canonical(m)).hexdigest(),
        "joint_inventory_sha256": hashlib.sha256(_canonical(joints)).hexdigest(),
        "joint_count": len(joints),
        "dof_count": total_dof,
        "asset_count": len(assets),
        "total_bytes": total_bytes,
        "authorization": "none_integrity_only",
    }
