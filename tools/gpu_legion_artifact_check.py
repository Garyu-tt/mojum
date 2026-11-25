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


def _open_windows_artifact(pathname: str) -> int:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    path = Path(os.path.abspath(pathname))
    if len(path.drive) != 2 or path.drive[1] != ":" or any(
        ":" in part for part in path.parts[1:]
    ):
        raise ArtifactError("model path must be a local drive path")

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    kernel.GetDriveTypeW.restype = wintypes.UINT
    local_drive_types = (2, 3, 5, 6)  # Removable, fixed, optical and RAM disks.
    if kernel.GetDriveTypeW(path.anchor) not in local_drive_types:
        raise ArtifactError("model drive must be local")

    for parent in reversed(path.parents):
        if parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ArtifactError("model path must not traverse a reparse point")

    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileType.argtypes = (wintypes.HANDLE,)
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    )
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD

    generic_read, share_read, open_existing = 0x80000000, 1, 3
    open_reparse_point, allow_directory_handle = 0x00200000, 0x02000000
    handle = kernel.CreateFileW(
        "\\\\?\\" + str(path), generic_read, share_read, None, open_existing,
        open_reparse_point | allow_directory_handle, None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        file_type_disk, file_attribute_tag_info = 1, 9
        if kernel.GetFileType(handle) != file_type_disk:
            raise ArtifactError("model is not a regular file")
        # FILE_ATTRIBUTE_TAG_INFO has two DWORD fields, attributes and reparse tag.
        info = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(
            handle, file_attribute_tag_info, info, ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if info[0] & (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ArtifactError("model is not a regular file")

        # A local volume GUID also rules out a remote handle reached via an alias.
        # A one-character buffer is enough to check whether the required size exists.
        volume_name_guid, file_name_opened = 1, 8
        if not kernel.GetFinalPathNameByHandleW(
            handle, ctypes.create_unicode_buffer(1), 1, volume_name_guid | file_name_opened
        ):
            raise ArtifactError("model file must be on a local volume")
        descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_NOINHERIT)
        handle = None  # The descriptor now owns the Windows handle.
        return descriptor
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _open_artifact(pathname: str) -> int:
    if os.name == "nt":
        return _open_windows_artifact(pathname)
    # Fail closed on symlinks and special files even if the path is switched
    # between preflight and open(). These flags are required on Linux hosts.
    required_flags = ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required_flags):
        raise ArtifactError("secure descriptor flags are not available")
    return os.open(
        pathname,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
    )


def verify(path: str | Path, expected_sha256: str, expected_bytes: int) -> dict:
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ArtifactError("expected SHA-256 must be 64 lowercase hex characters")
    if type(expected_bytes) is not int or not 4 <= expected_bytes <= MAX_MODEL_BYTES:
        raise ArtifactError("expected byte count out of range")
    descriptor = _open_artifact(os.fspath(path))
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
