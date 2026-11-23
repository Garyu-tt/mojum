"""Offline, deterministic admission policy for staged VRM asset metadata.

This module validates metadata only. It never opens asset paths or performs I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.vrm-asset-policy/1"
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")


class AssetPolicyError(ValueError):
    """Raised when the policy document itself is malformed."""


def _exact_dict(value: Any) -> bool:
    return type(value) is dict


def _token(value: Any) -> bool:
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _safe_relative_path(value: Any) -> bool:
    if type(value) is not str or not value or len(value) > 240:
        return False
    if "\\" in value or value.startswith("/") or ":" in value:
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") and _TOKEN.fullmatch(part) for part in parts)


def _policy(policy: Any) -> dict[str, Any]:
    if not _exact_dict(policy):
        raise AssetPolicyError("policy must be an exact dict")
    required = {"allowed_dependencies", "allowed_licenses", "allowed_media_types",
                "max_asset_bytes", "max_dependencies"}
    if set(policy) != required:
        raise AssetPolicyError("policy has missing or unknown keys")
    for key in ("allowed_dependencies", "allowed_licenses", "allowed_media_types"):
        values = policy[key]
        if type(values) not in (list, tuple) or len(values) > 128:
            raise AssetPolicyError(f"{key} must be a bounded list or tuple")
        if any(not _token(item) for item in values) or len(set(values)) != len(values):
            raise AssetPolicyError(f"{key} contains invalid or duplicate tokens")
    if type(policy["max_asset_bytes"]) is not int or not 1 <= policy["max_asset_bytes"] <= 2**40:
        raise AssetPolicyError("max_asset_bytes must be an exact int in range")
    if type(policy["max_dependencies"]) is not int or not 0 <= policy["max_dependencies"] <= 128:
        raise AssetPolicyError("max_dependencies must be an exact int in range")
    return {
        "allowed_dependencies": tuple(sorted(policy["allowed_dependencies"])),
        "allowed_licenses": tuple(sorted(policy["allowed_licenses"])),
        "allowed_media_types": tuple(sorted(policy["allowed_media_types"])),
        "max_asset_bytes": policy["max_asset_bytes"],
        "max_dependencies": policy["max_dependencies"],
    }


def validate_manifest(manifest: Any, policy: Any) -> dict[str, Any]:
    """Return a detached deterministic report for caller-supplied metadata.

    The manifest schema is:
      {schema, provenance, asset:{path,sha256,size_bytes,media_type,source,license},
       dependencies:[{name,version,license}]}
    """
    rules = _policy(policy)
    if not _exact_dict(manifest):
        raise AssetPolicyError("manifest must be an exact dict")
    if set(manifest) != {"schema", "provenance", "asset", "dependencies"}:
        raise AssetPolicyError("manifest has missing or unknown keys")

    violations: list[str] = []
    if manifest["schema"] != SCHEMA or type(manifest["schema"]) is not str:
        violations.append("schema")
    if manifest["provenance"] != "PUBLIC_SYNTHETIC" or type(manifest["provenance"]) is not str:
        violations.append("provenance")

    asset = manifest["asset"]
    if not _exact_dict(asset) or set(asset) != {
        "path", "sha256", "size_bytes", "media_type", "source", "license"
    }:
        raise AssetPolicyError("asset must have the exact metadata keys")
    path, digest = asset["path"], asset["sha256"]
    size, media_type = asset["size_bytes"], asset["media_type"]
    source, license_id = asset["source"], asset["license"]
    if not _safe_relative_path(path):
        violations.append("asset.path")
    if type(digest) is not str or _HEX_256.fullmatch(digest) is None:
        violations.append("asset.sha256")
    if type(size) is not int or size <= 0 or size > rules["max_asset_bytes"]:
        violations.append("asset.size_bytes")
    if type(media_type) is not str or media_type not in rules["allowed_media_types"]:
        violations.append("asset.media_type")
    if not _token(source) or "://" in source:
        violations.append("asset.source")
    if type(license_id) is not str or license_id not in rules["allowed_licenses"]:
        violations.append("asset.license")

    dependencies = manifest["dependencies"]
    if type(dependencies) is not list or len(dependencies) > rules["max_dependencies"]:
        raise AssetPolicyError("dependencies must be a bounded exact list")
    normalized_dependencies: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for index, dependency in enumerate(dependencies):
        if not _exact_dict(dependency) or set(dependency) != {"name", "version", "license"}:
            raise AssetPolicyError(f"dependency[{index}] has invalid shape")
        name, version, dep_license = dependency["name"], dependency["version"], dependency["license"]
        if not _token(name):
            violations.append(f"dependencies[{index}].name")
            continue
        if name in seen_names:
            violations.append(f"dependencies[{index}].duplicate")
        seen_names.add(name)
        if name not in rules["allowed_dependencies"]:
            violations.append(f"dependencies[{index}].unapproved_name")
        if type(version) is not str or _VERSION.fullmatch(version) is None:
            violations.append(f"dependencies[{index}].version")
        if type(dep_license) is not str or dep_license not in rules["allowed_licenses"]:
            violations.append(f"dependencies[{index}].license")
        if (type(version) is str and _VERSION.fullmatch(version)
                and type(dep_license) is str and dep_license in rules["allowed_licenses"]):
            normalized_dependencies.append({"name": name, "version": version, "license": dep_license})

    normalized_dependencies.sort(key=lambda item: (item["name"], item["version"], item["license"]))
    normalized = {
        "schema": SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "asset": {
            "path": path if _safe_relative_path(path) else "",
            "sha256": digest if type(digest) is str and _HEX_256.fullmatch(digest) else "",
            "size_bytes": size if type(size) is int else 0,
            "media_type": media_type if type(media_type) is str else "",
            "source": source if _token(source) else "",
            "license": license_id if type(license_id) is str else "",
        },
        "dependencies": normalized_dependencies,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    report_digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    ordered = sorted(set(violations))
    return {
        "schema": SCHEMA,
        "accepted": not ordered,
        "violations": ordered,
        "manifest_digest": report_digest,
        "normalized": normalized,
    }
