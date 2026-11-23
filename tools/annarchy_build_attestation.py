#!/usr/bin/env python3
"""Offline, metadata-only attestation for caller-supplied ANNarchy CPU builds.

This module never imports ANNarchy, opens artifact paths, executes a compiler,
loads a binary, or performs network access. A successful result attests only
that a bounded manifest is well-formed and that its caller-supplied digests
are pinned into a deterministic receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any, Mapping

SCHEMA = "mojum.annarchy-build-manifest/1"
MAX_INPUT_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_BUILD_FLAGS = 32
MAX_THREADS = 64

_KEYS = {
    "schema", "package", "package_version", "backend", "compiler",
    "compiler_version", "target", "openmp", "threads", "build_flags",
    "source_revision", "model_sha256", "artifact_sha256",
    "artifact_size_bytes",
}
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\\.[0-9]+(?:[.+-][A-Za-z0-9.+-]+)?$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TARGETS = {"linux-x86_64", "linux-aarch64", "windows-x86_64", "macos-arm64"}
_COMPILERS = {"gcc", "clang", "msvc"}
_FLAG_RE = re.compile(
    r"^(?:-O[0-3]|-std=c\+\+17|-fopenmp|-DNDEBUG|"
    r"-march=[A-Za-z0-9_+.-]{1,64}|-mtune=[A-Za-z0-9_+.-]{1,64}|"
    r"/O[1-2]|/openmp)$"
)


class ManifestError(ValueError):
    """Raised when a build manifest is malformed or exceeds policy limits."""


def _exact_int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ManifestError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _string(value: Any, name: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or len(value) > 128:
        raise ManifestError(f"{name} must be a non-empty string of at most 128 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ManifestError(f"{name} contains a control character")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ManifestError(f"{name} has an invalid format")
    return value


def validate_manifest(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach one strict, bounded ANNarchy CPU build manifest."""
    if type(record) is not dict:
        raise ManifestError("manifest must be a JSON object")
    if set(record) != _KEYS:
        missing = sorted(_KEYS - set(record))
        extra = sorted(set(record) - _KEYS)
        raise ManifestError(f"manifest keys mismatch (missing={missing}, extra={extra})")

    if record["schema"] != SCHEMA:
        raise ManifestError("unsupported manifest schema")
    if record["package"] != "ANNarchy":
        raise ManifestError("package must be exactly 'ANNarchy'")

    package_version = _string(record["package_version"], "package_version", _VERSION_RE)
    compiler = _string(record["compiler"], "compiler")
    if compiler not in _COMPILERS:
        raise ManifestError("compiler must be gcc, clang, or msvc")
    compiler_version = _string(record["compiler_version"], "compiler_version", _VERSION_RE)
    target = _string(record["target"], "target")
    if target not in _TARGETS:
        raise ManifestError("target platform is not in the supported metadata profile")

    backend = _string(record["backend"], "backend")
    if backend not in {"cpu", "openmp"}:
        raise ManifestError("backend must be cpu or openmp")
    if type(record["openmp"]) is not bool:
        raise ManifestError("openmp must be a boolean")
    threads = _exact_int(record["threads"], "threads", 1, MAX_THREADS)
    if record["openmp"] is not (backend == "openmp"):
        raise ManifestError("backend and openmp flag disagree")
    if backend == "cpu" and threads != 1:
        raise ManifestError("the cpu backend must declare exactly one thread")
    if backend == "openmp" and threads < 2:
        raise ManifestError("the openmp backend must declare at least two threads")

    flags = record["build_flags"]
    if type(flags) is not list or len(flags) > MAX_BUILD_FLAGS:
        raise ManifestError(f"build_flags must be a list of at most {MAX_BUILD_FLAGS} entries")
    detached_flags: list[str] = []
    for index, flag in enumerate(flags):
        item = _string(flag, f"build_flags[{index}]")
        if _FLAG_RE.fullmatch(item) is None:
            raise ManifestError(f"build_flags[{index}] is not in the safe metadata allowlist")
        if item in detached_flags:
            raise ManifestError("build_flags must not contain duplicates")
        detached_flags.append(item)

    source_revision = _string(record["source_revision"], "source_revision", _HEX40_RE)
    model_sha256 = _string(record["model_sha256"], "model_sha256", _HEX64_RE)
    artifact_sha256 = _string(record["artifact_sha256"], "artifact_sha256", _HEX64_RE)
    artifact_size = _exact_int(
        record["artifact_size_bytes"], "artifact_size_bytes", 1, MAX_ARTIFACT_BYTES
    )

    # Return a fresh object in a fixed field layout; never retain caller objects.
    return {
        "schema": SCHEMA,
        "package": "ANNarchy",
        "package_version": package_version,
        "backend": backend,
        "compiler": compiler,
        "compiler_version": compiler_version,
        "target": target,
        "openmp": record["openmp"],
        "threads": threads,
        "build_flags": detached_flags,
        "source_revision": source_revision,
        "model_sha256": model_sha256,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_manifest_json(data: bytes | str) -> dict[str, Any]:
    """Decode at most 64 KiB of strict UTF-8 JSON and validate its manifest."""
    if type(data) is bytes:
        if len(data) > MAX_INPUT_BYTES:
            raise ManifestError("manifest JSON exceeds the 64 KiB input limit")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManifestError("manifest JSON must be UTF-8") from exc
    elif type(data) is str:
        try:
            encoded = data.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ManifestError("manifest JSON contains invalid Unicode") from exc
        if len(encoded) > MAX_INPUT_BYTES:
            raise ManifestError("manifest JSON exceeds the 64 KiB input limit")
        text = data
    else:
        raise ManifestError("manifest input must be bytes or text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ManifestError(f"non-finite JSON number is forbidden: {token}")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError("invalid manifest JSON") from exc
    return validate_manifest(value)


def canonical_json(manifest: Mapping[str, Any]) -> bytes:
    """Return stable UTF-8 JSON for a validated manifest."""
    checked = validate_manifest(manifest)
    return json.dumps(
        checked, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def attest_build(record: Mapping[str, Any]) -> dict[str, Any]:
    """Create a deterministic metadata-only SHA-256 attestation receipt."""
    manifest = validate_manifest(record)
    encoded = canonical_json(manifest)
    return {
        "schema": "mojum.annarchy-build-attestation/1",
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "claim": "metadata-only; caller-supplied digests are not independently measured",
        "limitations": [
            "does not inspect or execute the generated artifact",
            "does not prove compiler reproducibility or ABI compatibility",
            "does not prove binary safety or neural correctness",
            "does not authenticate the manifest author or build environment",
        ],
    }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and attest one bounded ANNarchy build manifest from stdin."
    )
    parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ManifestError("manifest JSON exceeds the 64 KiB input limit")
        result = attest_build(parse_manifest_json(raw))
    except (ManifestError, OSError) as exc:
        print(f"annarchy-build-attestation: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, indent=2) + "\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
