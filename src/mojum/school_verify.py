"""Read-only cold verification of R0.2 PRIVATE scripted-school artifacts.

This checks exact reproducibility against the supplied catalog and the
deterministic fixture generator. It is not an authenticated signature,
proof of human learning, or protection against a malicious same-UID writer.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import json
import os
from itertools import islice
from pathlib import Path
import stat
from typing import Any

from .school import (
    MAX_ARTIFACT_BYTES,
    SchoolContractError,
    _canonical_json,
    _validated_catalog_bytes,
    load_catalog,
    run_scripted_activity,
)

VERIFY_SCHEMA = "mojum.school-fixture-verification/1"
MAX_MANIFEST_BYTES = 65_536


@contextmanager
def _fixture_directory(target: Path):
    """Pin one real POSIX directory across enumeration and all file reads.

    On non-POSIX systems retain the existing best-effort path checks; those
    platforms do not receive a claim of race-resistant cold verification.
    """
    if os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"):
        try:
            fd = os.open(
                target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise SchoolContractError("fixture must be a real directory") from error
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise SchoolContractError("fixture must be a real directory")
            yield fd
        finally:
            os.close(fd)
    else:
        try:
            if target.is_symlink() or not target.is_dir():
                raise SchoolContractError("fixture must be a real directory")
        except OSError as error:
            raise SchoolContractError("cannot inspect school fixture directory") from error
        yield None


def _read_regular_bounded(path: Path, limit: int, *, directory_fd: int | None = None) -> bytes:
    """Open a bounded regular file, relative to a pinned directory on POSIX."""
    try:
        if directory_fd is not None:
            # O_NOFOLLOW prevents swapping the entry to a symlink after a
            # directory listing. O_NONBLOCK prevents a substituted FIFO from
            # stalling the verifier before fstat can reject it.
            flags = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0))
            with os.fdopen(os.open(path.name, flags, dir_fd=directory_fd), "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise SchoolContractError("school fixture contains a nonregular file")
                payload = source.read(limit + 1)
        else:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise SchoolContractError("school fixture contains a nonregular file")
            with path.open("rb") as source:
                payload = source.read(limit + 1)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise SchoolContractError("school fixture contains a nonregular file") from error
        raise SchoolContractError("cannot read school fixture file") from error
    if len(payload) > limit:
        raise SchoolContractError("school fixture file exceeds byte budget")
    return payload


def verify_scripted_fixture(
    output_dir: str | Path, catalog: dict[str, Any],
) -> dict[str, Any]:
    """Verify a cold, scripted PRIVATE fixture without changing any file.

    A trusted catalog is required. The expected events, PRIVATE classification,
    exact canonical JSON bytes, artwork/music bytes and their SHA-256 links are
    regenerated. No PRIVATE event or artifact is included in the result.
    """
    _validated_catalog_bytes(catalog)
    target = Path(output_dir)
    # Keep the directory descriptor open for the *entire* comparison, not just
    # the listing. A concurrent rename must not redirect later artifact reads.
    with _fixture_directory(target) as directory_fd:
        return _verify_pinned_fixture(target, catalog, directory_fd)


def _verify_pinned_fixture(
    target: Path, catalog: dict[str, Any], directory_fd: int | None,
) -> dict[str, Any]:
    try:
        if directory_fd is None:
            names = [path.name for path in islice(target.iterdir(), 4)]
        else:
            with os.scandir(directory_fd) as entries:
                names = [entry.name for entry in islice(entries, 4)]
    except OSError as error:
        raise SchoolContractError("cannot list school fixture directory") from error

    manifest_wire = _read_regular_bounded(
        target / "events.json", MAX_MANIFEST_BYTES, directory_fd=directory_fd,
    )
    try:
        manifest = json.loads(manifest_wire.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise SchoolContractError("invalid school fixture manifest") from error
    if not isinstance(manifest, dict):
        raise SchoolContractError("invalid school fixture manifest shape")
    events = manifest.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise SchoolContractError("missing school fixture event sequence")
    section_id = manifest.get("section_id")
    mode = manifest.get("mode")
    start_tick = events[0].get("sim_tick")

    expected = run_scripted_activity(catalog, section_id, start_tick=start_tick, mode=mode)
    expected_manifest = {k: v for k, v in expected.items() if k != "artifacts"}
    if manifest_wire != _canonical_json(expected_manifest):
        raise SchoolContractError("school fixture manifest differs from deterministic replay")

    expected_names = {"events.json", *expected["artifacts"].keys()}
    if len(names) != len(expected_names) or set(names) != expected_names:
        raise SchoolContractError("school fixture directory has missing or extra files")
    total_bytes = len(manifest_wire)
    for name, original in expected["artifacts"].items():
        actual = _read_regular_bounded(
            target / name, MAX_ARTIFACT_BYTES, directory_fd=directory_fd,
        )
        if actual != original:
            raise SchoolContractError("school fixture artifact differs from deterministic replay")
        total_bytes += len(actual)
    return {
        "schema": VERIFY_SCHEMA,
        "status": "MATCHES_SCRIPTED_REPLAY",
        "section_id": section_id,
        "mode": mode,
        "start_tick": start_tick,
        "file_count": len(expected_names),
        "verified_bytes": total_bytes,
        "privacy": "PRIVATE",
        "physical_actuation": False,
        "trust": "local_integrity_only_not_authenticated",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only cold check of PRIVATE scripted school artifacts")
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_scripted_fixture(args.fixture, load_catalog(args.catalog))
    except (SchoolContractError, OSError) as error:
        parser.error(str(error))
    print(_canonical_json(result).decode("utf-8").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
