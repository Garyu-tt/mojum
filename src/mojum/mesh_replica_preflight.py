"""Offline, metadata-only replica comparison. Never grants write or failover rights.

This optional tool compares *claims* supplied by two manifest files. It does
not authenticate manifests, inspect journals, prove persistence, or elect a
leader. A matching result is NOT a promotion authorization.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

SCHEMA = "mojum.mesh.replica-head/1"
MAX_MANIFEST_BYTES = 4096
MAX_COUNTER = (1 << 63) - 1
_ID = re.compile(r"[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)*", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_KEYS = frozenset({
    "schema", "organism_id", "node_id", "epoch", "sequence",
    "journal_head_sha256", "checkpoint_sha256",
})


class ManifestError(ValueError):
    """A proposed replica manifest violates the strict wire contract."""


@dataclass(frozen=True)
class ReplicaHead:
    organism_id: str
    node_id: str
    epoch: int
    sequence: int
    journal_head_sha256: str
    checkpoint_sha256: str


@dataclass(frozen=True)
class Decision:
    # match/stale/diverged are statements about *untrusted metadata only*.
    status: str
    reason: str
    authorizes_promotion: bool = False
    requires_external_fencing: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "schema": "mojum.mesh.replica-preflight-result/1",
            "status": self.status,
            "reason": self.reason,
            "authorizes_promotion": self.authorizes_promotion,
            "requires_external_fencing": self.requires_external_fencing,
        }


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate JSON field")
        result[key] = value
    return result


def _bad_constant(_value: str) -> None:
    raise ManifestError("nonfinite JSON constant")


def parse_manifest(payload: bytes) -> ReplicaHead:
    """Parse bounded UTF-8 JSON. No compatibility fallbacks or hidden fields."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        raise ManifestError("manifest byte budget exceeded or empty")
    try:
        obj = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_keys,
            parse_constant=_bad_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ManifestError("invalid strict JSON manifest") from exc
    if type(obj) is not dict or obj.keys() != _KEYS:
        raise ManifestError("invalid manifest fields")
    if type(obj["schema"]) is not str or obj["schema"] != SCHEMA:
        raise ManifestError("unsupported manifest schema")
    for key in ("organism_id", "node_id"):
        value = obj[key]
        if type(value) is not str or not 1 <= len(value) <= 128 or _ID.fullmatch(value) is None:
            raise ManifestError("invalid identifier")
    for key in ("epoch", "sequence"):
        value = obj[key]
        if type(value) is not int or not 1 <= value <= MAX_COUNTER:
            raise ManifestError("invalid positive counter")
    for key in ("journal_head_sha256", "checkpoint_sha256"):
        value = obj[key]
        if type(value) is not str or _DIGEST.fullmatch(value) is None:
            raise ManifestError("invalid lowercase SHA-256 digest")
    return ReplicaHead(
        organism_id=obj["organism_id"], node_id=obj["node_id"],
        epoch=obj["epoch"], sequence=obj["sequence"],
        journal_head_sha256=obj["journal_head_sha256"],
        checkpoint_sha256=obj["checkpoint_sha256"],
    )


def read_manifest(path: str | os.PathLike[str]) -> ReplicaHead:
    """Read one regular file only; reject symlink path segments and oversized files.

    This protects accidental unsafe reads, not hostile concurrent filesystem
    replacement. The caller must establish file custody independently.
    """
    try:
        full_path = Path(os.path.abspath(os.fspath(path)))
        for component in (full_path, *full_path.parents):
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ManifestError("symlinked manifest path")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(full_path, flags)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode):
                raise ManifestError("not a regular manifest file")
            if not 1 <= meta.st_size <= MAX_MANIFEST_BYTES:
                raise ManifestError("manifest file size outside budget")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_MANIFEST_BYTES + 1)
        finally:
            os.close(fd)
    except ManifestError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ManifestError("cannot securely read manifest") from exc
    return parse_manifest(data)


def compare(source: ReplicaHead, replica: ReplicaHead) -> Decision:
    """Assess *metadata consistency* only, with no authorizing side effects.

    Source should be an independently chosen trusted reference *outside* this
    function; even a matched receipt needs real archive verification, quorum,
    authorization and durable leader fencing before any write promotion.
    """
    if type(source) is not ReplicaHead or type(replica) is not ReplicaHead:
        raise ManifestError("invalid in-memory manifest types")
    # Revalidate even in-memory instances (not just parsed JSON).
    for item in (source, replica):
        encoded = {
            "schema": SCHEMA, "organism_id": item.organism_id,
            "node_id": item.node_id, "epoch": item.epoch,
            "sequence": item.sequence,
            "journal_head_sha256": item.journal_head_sha256,
            "checkpoint_sha256": item.checkpoint_sha256,
        }
        try:
            parse_manifest(json.dumps(encoded, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ManifestError("invalid in-memory manifest") from exc

    if source.organism_id != replica.organism_id:
        return Decision("diverged", "organism_mismatch")
    if source.node_id == replica.node_id:
        return Decision("diverged", "not_independent_node_ids")
    if source.epoch != replica.epoch:
        return Decision("diverged", "epoch_mismatch_requires_fencing")
    if replica.sequence > source.sequence:
        return Decision("diverged", "replica_ahead_of_reference")
    if replica.sequence < source.sequence:
        return Decision("stale", "replica_behind_unproven_ancestry")
    if source.journal_head_sha256 != replica.journal_head_sha256:
        return Decision("diverged", "journal_head_mismatch")
    if source.checkpoint_sha256 != replica.checkpoint_sha256:
        return Decision("diverged", "checkpoint_digest_mismatch")
    return Decision("match", "metadata_equal_requires_independent_verification")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="reference metadata JSON path")
    parser.add_argument("--replica", required=True, help="replica metadata JSON path")
    args = parser.parse_args(argv)
    try:
        decision = compare(read_manifest(args.source), read_manifest(args.replica))
    except ManifestError:
        # No user-supplied paths or input content in output.
        print(json.dumps({
            "schema": "mojum.mesh.replica-preflight-result/1",
            "status": "invalid", "reason": "invalid_manifest",
            "authorizes_promotion": False, "requires_external_fencing": True,
        }, sort_keys=True))
        return 1
    print(json.dumps(decision.as_dict(), sort_keys=True))
    return {"match": 0, "stale": 2, "diverged": 1}[decision.status]


if __name__ == "__main__":
    raise SystemExit(main())
