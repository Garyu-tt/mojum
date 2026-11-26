"""Offline, bounded v1/v2 graph audit and explicit no-clobber v1 <-> v2 export.

No graph execution, journal mutation, permission grant, or PRIVATE-data export
occurs without the operator explicitly choosing a new output path.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

from .graph import GraphError, SCHEMA as V1_SCHEMA, _check_json_depth
from .graph_v2 import V2_SCHEMA, downgrade_v2, upgrade_v1, validate_v2

REPORT_SCHEMA = "mojum.graph-audit-report/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_LABEL_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GraphError("duplicate graph JSON key")
        result[key] = value
    return result


def _nonfinite_constant(value):
    raise GraphError("non-finite JSON number is not permitted")


def _read_json(path: Path, limit: int):
    """Bound bytes and nesting before UTF-8/JSON parsing, with no silent key collapsing."""
    # A byte cap cannot bound a blocking FIFO open/read. Open nonblocking
    # first, then inspect the actual descriptor before reading any bytes.
    # Regular-file symlinks remain supported; a symlink to a FIFO/device is
    # rejected by the same descriptor check without waiting for a writer.
    if os.name == "posix" and not getattr(os, "O_NONBLOCK", 0):
        raise GraphError("cannot safely read graph or labels without O_NONBLOCK")
    descriptor = -1
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_BINARY", 0))
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GraphError("graph or labels input must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > limit:
        raise GraphError("graph or labels file exceeds byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
        _check_json_depth(text)
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_nonfinite_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, GraphError):
            raise
        raise GraphError("invalid UTF-8 JSON input") from exc


def _audit(graph):
    """Return non-sensitive shape/provenance statistics, never graph contents."""
    if not isinstance(graph, dict):
        raise GraphError("graph must be a JSON object")
    if graph.get("schema") == V1_SCHEMA:
        checked = validate_v2(upgrade_v1(graph))
        wire_sha = checked["origin_sha256"]
    elif graph.get("schema") == V2_SCHEMA:
        checked = validate_v2(graph)
        wire_sha = checked["graph_sha256"]
    else:
        raise GraphError("unsupported graph schema")
    return {
        "schema": REPORT_SCHEMA,
        "input_schema": graph["schema"],
        "input_sha256": wire_sha,
        "origin_v1_sha256": checked["origin_sha256"],
        "node_count": checked["node_count"],
        "edge_count": checked["edge_count"],
        "execution": "NOT_PERFORMED",
        "visibility_security": "METADATA_ONLY_NOT_ACCESS_CONTROL",
    }


def _write_new_private(path: Path, payload: bytes):
    """Stage 0600, fsync, then atomically create final name without replacing it.

    Hard-link publication is atomic for the target name and fails if it exists.
    The operator controls the parent directory; this is not a hostile-UID
    directory-security boundary or a durable distributed transaction.
    """
    if len(payload) > MAX_OUTPUT_BYTES:
        raise GraphError("upgraded graph exceeds byte limit")
    fd, staging = tempfile.mkstemp(prefix=".mojum-graph-v2-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-clobber publication: never replace a previous export.
        os.link(staging, path)
    finally:
        os.unlink(staging)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded offline graph audit and explicit v1 <-> v2 metadata-only export"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("audit", "upgrade", "downgrade"):
        sub = commands.add_parser(command)
        sub.add_argument("--input", type=Path, required=True)
        if command in ("upgrade", "downgrade"):
            sub.add_argument("--output", type=Path, required=True)
        if command == "upgrade":
            sub.add_argument("--labels", type=Path, help="optional private JSON node-ID to visibility map")
    args = parser.parse_args(argv)
    try:
        graph = _read_json(args.input, MAX_INPUT_BYTES)
        if args.command == "audit":
            result = _audit(graph)
        elif args.command == "upgrade":
            if not isinstance(graph, dict) or graph.get("schema") != V1_SCHEMA:
                raise GraphError("upgrade requires canonical v1 graph input")
            labels = _read_json(args.labels, MAX_LABEL_BYTES) if args.labels is not None else None
            upgraded = upgrade_v1(graph, labels=labels)
            checked = validate_v2(upgraded)
            payload = (json.dumps(
                upgraded, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            ) + "\n").encode("utf-8")
            _write_new_private(args.output, payload)
            result = {
                "schema": REPORT_SCHEMA,
                "input_schema": V1_SCHEMA,
                "output_schema": V2_SCHEMA,
                "input_sha256": checked["origin_sha256"],
                "output_sha256": checked["graph_sha256"],
                "node_count": checked["node_count"],
                "edge_count": checked["edge_count"],
                "execution": "NOT_PERFORMED",
                "visibility_security": "METADATA_ONLY_NOT_ACCESS_CONTROL",
            }
        else:
            if not isinstance(graph, dict) or graph.get("schema") != V2_SCHEMA:
                raise GraphError("downgrade requires canonical v2 graph input")
            checked = validate_v2(graph)
            downgraded = downgrade_v2(graph)
            payload = (json.dumps(
                downgraded, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            ) + "\n").encode("utf-8")
            _write_new_private(args.output, payload)
            result = {
                "schema": REPORT_SCHEMA,
                "input_schema": V2_SCHEMA,
                "output_schema": V1_SCHEMA,
                "input_sha256": checked["graph_sha256"],
                "output_sha256": checked["origin_sha256"],
                "node_count": checked["node_count"],
                "edge_count": checked["edge_count"],
                "execution": "NOT_PERFORMED",
                "visibility_security": "METADATA_ONLY_NOT_ACCESS_CONTROL",
            }
    except (GraphError, OSError) as exc:
        parser.exit(2, f"graph audit rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
