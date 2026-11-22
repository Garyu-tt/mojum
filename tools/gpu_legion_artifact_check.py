#!/usr/bin/env python3
"""Verify a local GGUF against an operator-pinned size and SHA-256.

Read-only: no network access, downloads, model execution, GPU access, or service
changes. A successful receipt proves file bytes only, not model compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

CHUNK_BYTES = 8 * 1024 * 1024
MAX_MODEL_BYTES = 64 * 1024**3
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactError(ValueError):
    """The file does not match the pinned artifact."""


def verify(path: str | Path, expected_sha256: str, expected_bytes: int) -> dict:
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ArtifactError("expected SHA-256 must be 64 lowercase hex characters")
    if type(expected_bytes) is not int or not 4 <= expected_bytes <= MAX_MODEL_BYTES:
        raise ArtifactError("expected byte count out of range")
    pathname = os.fspath(path)
    # Fail closed on symlinks and special files even if the path is switched
    # between preflight and open(). These flags are required on Linux hosts.
    required_flags = ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required_flags):
        raise ArtifactError("secure descriptor flags are not available")
    descriptor = os.open(
        pathname,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactError("model is not a regular file")
        if before.st_size != expected_bytes:
            raise ArtifactError(
                "model size mismatch: observed %d, expected %d" %
                (before.st_size, expected_bytes)
            )
        hasher = hashlib.sha256()
        processed = 0
        magic = bytearray()
        while True:
            block = os.read(descriptor, min(CHUNK_BYTES, expected_bytes - processed))
            if not block:
                break
            if len(magic) < 4:
                magic.extend(block[:4-len(magic)])
            hasher.update(block)
            processed += len(block)
            if processed > expected_bytes:
                raise ArtifactError("model changed length while reading")
            if processed == expected_bytes:
                # Do not read arbitrary extra bytes if the file was replaced.
                break
        after = os.fstat(descriptor)
        if bytes(magic) != b"GGUF":
            raise ArtifactError("model does not start with GGUF magic")
        if (processed != expected_bytes or after.st_size != before.st_size or
                after.st_mtime_ns != before.st_mtime_ns or
                after.st_ctime_ns != before.st_ctime_ns or
                after.st_ino != before.st_ino or after.st_dev != before.st_dev):
            raise ArtifactError("model changed during verification")
        observed = hasher.hexdigest()
        if observed != expected_sha256:
            raise ArtifactError("model SHA-256 mismatch")
        return {
            "schema": "mojum.gpu-legion-artifact.v1",
            "verified": True,
            "observed_sha256": observed,
            "observed_bytes": processed,
            "format": "GGUF",
            "launch_performed": False,
            "runtime_compatible": False,
            "independent_source_authenticated": False,
        }
    finally:
        os.close(descriptor)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="local model file; never downloaded")
    parser.add_argument("--sha256", required=True, help="independently obtained pinned LFS SHA-256")
    parser.add_argument("--bytes", type=int, required=True, help="independently obtained exact file length")
    args = parser.parse_args(argv)
    try:
        result = verify(args.file, args.sha256, args.bytes)
    except (ArtifactError, OSError) as exc:
        print(json.dumps({"schema": "mojum.gpu-legion-artifact.v1",
                          "verified": False, "launch_performed": False,
                          "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
