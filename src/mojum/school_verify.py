"""Read-only cold verification of R0.2 PRIVATE scripted-school artifacts.

This checks exact reproducibility against the supplied catalog and the
deterministic fixture generator. It is not an authenticated signature,
proof of human learning, or protection against a malicious same-UID writer.
"""
from __future__ import annotations

import argparse
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


def _read_regular_bounded(path: Path, limit: int) -> bytes:
    """Read one checked inode, not a pathname reopened after validation.

    O_NOFOLLOW rejects a symlink inserted after lstat on supporting systems;
    O_NONBLOCK prevents a swapped-in FIFO from stalling the cold verifier.
    Comparing file identity and checking the opened descriptor also protects
    platforms without O_NOFOLLOW against ordinary pathname replacement.
    This is not a defense against a malicious writer mutating the same inode.
    """
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise SchoolContractError("school fixture contains a nonregular file")
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise SchoolContractError("school fixture file changed while opening")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = -1  # The file object now owns and closes the descriptor.
                payload = source.read(limit + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except OSError as error:
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
    try:
        if target.is_symlink() or not target.is_dir():
            raise SchoolContractError("fixture must be a real directory")
        paths = list(islice(target.iterdir(), 4))  # Only three files are permitted.
    except OSError as error:
        raise SchoolContractError("cannot list school fixture directory") from error

    manifest_path = target / "events.json"
    manifest_wire = _read_regular_bounded(manifest_path, MAX_MANIFEST_BYTES)
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

    # Invalid tick, section or mode fail through the same public contract as
    # fixture creation. In particular bool is never a valid virtual tick.
    expected = run_scripted_activity(catalog, section_id, start_tick=start_tick, mode=mode)
    expected_manifest = {k: v for k, v in expected.items() if k != "artifacts"}
    if manifest_wire != _canonical_json(expected_manifest):
        raise SchoolContractError("school fixture manifest differs from deterministic replay")

    expected_names = {"events.json", *expected["artifacts"].keys()}
    if len(paths) != len(expected_names) or {p.name for p in paths} != expected_names:
        raise SchoolContractError("school fixture directory has missing or extra files")
    total_bytes = len(manifest_wire)
    for name, original in expected["artifacts"].items():
        actual = _read_regular_bounded(target / name, MAX_ARTIFACT_BYTES)
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
