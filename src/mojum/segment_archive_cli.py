"""Explicit offline CLI for PRIVATE physical sealed-segment archives.

Never prints PRIVATE event payload, filenames, learned state or HMAC keys.
No default export: the operator must pass --include-private.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .segment_archive import ArchiveError, export_sealed_segments
from .segment_archive_verify import verify_sealed_archive, restore_into_new_sqlite
from .segmented_noosphere import SegmentedNoosphere
from .noosphere import NoosphereError


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mojum experimental PRIVATE sealed segment archive tool"
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    make = sub.add_parser("export", help="explicitly copy sealed segments")
    make.add_argument("--db", type=Path, required=True)
    make.add_argument("--agent-id", required=True)
    make.add_argument("--segment-size", type=int, required=True)
    make.add_argument("--archive-dir", type=Path, required=True)
    make.add_argument("--expected-source-head", required=True)
    make.add_argument("--include-private", action="store_true",
                      help="explicitly authorize copying PRIVATE plaintext")
    check = sub.add_parser("verify", help="read-only verify archive without SQLite")
    check.add_argument("--archive-dir", type=Path, required=True)
    check.add_argument("--expected-sealed-head", required=True)
    recover = sub.add_parser("restore", help="publish NEW SQLite from sealed prefix")
    recover.add_argument("--archive-dir", type=Path, required=True)
    recover.add_argument("--new-db", type=Path, required=True)
    recover.add_argument("--expected-sealed-head", required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            if not args.include_private:
                parser.error("export of PRIVATE history requires --include-private")
            with SegmentedNoosphere(
                args.db, args.agent_id, segment_size=args.segment_size,
            ) as source:
                status = export_sealed_segments(
                    source, args.archive_dir,
                    include_nonpublic=args.include_private,
                    external_expected_head=args.expected_source_head,
                )
        elif args.operation == "verify":
            status = verify_sealed_archive(
                args.archive_dir,
                external_expected_head=args.expected_sealed_head,
            )
        else:
            status = restore_into_new_sqlite(
                args.archive_dir, args.new_db,
                external_expected_head=args.expected_sealed_head,
            )
    except (ArchiveError, NoosphereError) as exc:
        parser.error(str(exc))
    # Do not expose PRIVATE data or even absolute source/destination paths.
    status.pop("restored_path", None)
    print(json.dumps(
        {"operation": args.operation, **status},
        ensure_ascii=False, sort_keys=True, allow_nan=False,
    ))
    return status


if __name__ == "__main__":
    main()
