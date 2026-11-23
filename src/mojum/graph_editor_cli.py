"""Offline CLI for the optional graph editor bridge; never writes canonical state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph import GraphError
from .graph_audit_cli import _read_json, _write_new_private
from .graph_editor_bridge import (
    MAX_WIRE_BYTES, export_editor, import_editor,
)


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in offline React Flow / Rete editor JSON; NO graph execution"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="new owner-only editor envelope")
    export.add_argument("--input", required=True, type=Path)
    export.add_argument("--backend", required=True, choices=("reactflow", "rete"))
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--allow-private", action="store_true")
    verify = commands.add_parser("verify", help="validate detached editor proposal")
    verify.add_argument("--input", required=True, type=Path)
    verify.add_argument("--expected-revision", required=True)
    verify.add_argument("--expected-sha256", required=True)
    verify.add_argument("--new-revision")
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--allow-private", action="store_true")
    args = parser.parse_args(argv)
    try:
        obj = _read_json(args.input, MAX_WIRE_BYTES)
        if args.command == "export":
            envelope = export_editor(obj, args.backend,
                                     allow_private=args.allow_private)
            result = {"schema": "mojum.graph-editor-report/1",
                      "backend": args.backend,
                      "base_revision": envelope["revision"],
                      "base_sha256": envelope["source_sha256"],
                      "nodes": len(envelope["nodes"]),
                      "edges": len(envelope["edges"]),
                      "execution": "NOT_PERFORMED"}
            _write_new_private(args.output, _bytes(envelope))
        else:
            candidate = import_editor(
                obj, expected_revision=args.expected_revision,
                expected_sha256=args.expected_sha256,
                new_revision=args.new_revision, allow_private=args.allow_private
            )
            output = {"schema": "mojum.graph-editor-candidate/1", **candidate}
            _write_new_private(args.output, _bytes(output))
            result = {"schema": "mojum.graph-editor-report/1",
                      "notes_changed": candidate["notes_changed"],
                      "new_revision": candidate["graph"]["revision"],
                      "execution": "NOT_PERFORMED",
                      "hot_swap": "NOT_PERFORMED"}
    except (GraphError, OSError) as exc:
        parser.exit(2, f"graph editor rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
