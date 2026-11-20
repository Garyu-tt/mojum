"""Offline, CPU-only handoff of *fixture component bytes* between local devices.

This is not a live Aquarium snapshot, authenticated transfer, encrypted backup,
multi-writer store, or atomic installation into a running organism. The caller
supplies five already-consistent component byte streams. No network is used.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .checkpoint_bundle import (
    MAX_COMPONENT_BYTES, NAMES, dumps, loads, pack, unpack,
)

MAX_WIRE_BYTES = 30_000_000
FILE_NAMES = {name: name + ".bin" for name in NAMES}


def _target(path):
    dest = Path(path)
    if not dest.name or not dest.parent.is_dir() or dest.parent.is_symlink():
        raise ValueError("destination requires an existing trusted local parent directory")
    return dest


def _read_limited(path, limit):
    """Read a regular file, rejecting final-component symlinks and large inputs."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("symbolic-link input is not accepted")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as inp:
        meta = os.fstat(inp.fileno())
        if not stat.S_ISREG(meta.st_mode):
            raise ValueError("input must be a regular file")
        if meta.st_size > limit:
            raise ValueError("input exceeds size limit")
        data = inp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input exceeds size limit")
    return data


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def export_new_archive(organism_id, revision, paths, destination):
    """Export exactly five input files into one canonical, never-overwritten wire file."""
    if type(paths) is not dict or set(paths) != set(NAMES):
        raise ValueError("five named component file paths required")
    parts = {name: _read_limited(paths[name], MAX_COMPONENT_BYTES) for name in NAMES}
    bundle = pack(organism_id, revision, parts)
    data = dumps(bundle).encode("ascii")
    if len(data) > MAX_WIRE_BYTES:
        raise ValueError("archive exceeds wire size limit")
    dest = _target(destination)
    if os.path.lexists(dest):
        raise ValueError("archive destination already exists")
    # Publish complete bytes via a hard link, which fails if the name exists.
    # Unlike os.replace(), it cannot overwrite a concurrent destination.
    fd, tmp_name = tempfile.mkstemp(prefix=".mojum-export-", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp_name, dest)
        except FileExistsError as exc:
            raise ValueError("archive destination already exists") from exc
    finally:
        os.unlink(tmp_name)
    return bundle["manifest"]


def read_verified_archive(path, *, expected_organism_id, expected_revision=None):
    """Validate canonical wire, all digests, and explicit destination identity."""
    if type(expected_organism_id) is not str or not expected_organism_id:
        raise ValueError("expected_organism_id must be a nonempty string")
    raw = _read_limited(path, MAX_WIRE_BYTES)
    try:
        wire = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("archive must be canonical ASCII JSON") from exc
    bundle = loads(
        wire, expected_revision=expected_revision,
        expected_organism_id=expected_organism_id,
    )
    parts = unpack(
        bundle, expected_revision=expected_revision,
        expected_organism_id=expected_organism_id,
    )
    return bundle["manifest"], parts


def restore_new_directory(archive, destination, *, expected_organism_id, expected_revision=None):
    """Stage verified bytes and publish a new directory; never restore a live runtime.

    The destination parent must be trusted and single-writer. The portable
    Python rename used here is not a no-replace atomic primitive against a
    racing *local* writer. No durability or cross-filesystem guarantee.
    """
    manifest, parts = read_verified_archive(
        archive, expected_organism_id=expected_organism_id,
        expected_revision=expected_revision,
    )
    dest = _target(destination)
    if os.path.lexists(dest):
        raise ValueError("restore destination already exists")
    stage = Path(tempfile.mkdtemp(prefix=".mojum-restore-", dir=dest.parent))
    try:
        for name in NAMES:
            _write_private(stage / FILE_NAMES[name], parts[name])
        metadata = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
        _write_private(stage / "manifest.json", metadata)
        if os.path.lexists(dest):
            raise ValueError("restore destination already exists")
        os.rename(stage, dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest


def _summary(manifest, *, location):
    return {
        "status": "VERIFIED_FIXTURE_COMPONENTS_NOT_LIVE_RESTORE",
        "location": str(location),
        "organism_id": manifest["organism_id"],
        "revision": manifest["revision"],
        "manifest_sha256": manifest["manifest_sha256"],
        "component_sha256": manifest["components"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mojum offline fixture checkpoint handoff (no network)")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write a new portable archive from five named byte files")
    export.add_argument("--organism-id", required=True)
    export.add_argument("--revision", type=int, required=True)
    export.add_argument("--output", required=True, type=Path)
    for name in NAMES:
        export.add_argument("--" + name, required=True, type=Path)
    for command in ("verify", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("--archive", type=Path, required=True)
        sub.add_argument("--organism-id", required=True)
        sub.add_argument("--revision", type=int)
        if command == "restore":
            sub.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            files = {name: getattr(args, name) for name in NAMES}
            manifest = export_new_archive(args.organism_id, args.revision, files, args.output)
            location = args.output
        elif args.command == "verify":
            manifest, _ = read_verified_archive(
                args.archive, expected_organism_id=args.organism_id,
                expected_revision=args.revision,
            )
            location = args.archive
        else:
            manifest = restore_new_directory(
                args.archive, args.directory, expected_organism_id=args.organism_id,
                expected_revision=args.revision,
            )
            location = args.directory
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(_summary(manifest, location=location), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
