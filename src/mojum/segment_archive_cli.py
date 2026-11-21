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
from .live_tail_capsule import (
    capture_live_tail, verify_point_in_time, restore_point_in_time,
)
from .archive_root_receipt import (
    ReceiptError, sign_owner_receipt, verify_owner_receipt,
    verify_two_archive_copies,
)
from .archive_device_transfer import (
    TransferError, copy_pinned_archive_to_device,
)
from .verified_replica_restore import recover_pinned_replica
from .quarantined_observer import QuarantinedObserver, ObserverError
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
    capture = sub.add_parser(
        "capture-tail", help="explicitly save exact unsealed PRIVATE journal tail"
    )
    capture.add_argument("--db", type=Path, required=True)
    capture.add_argument("--agent-id", required=True)
    capture.add_argument("--segment-size", type=int, required=True)
    capture.add_argument("--archive-dir", type=Path, required=True)
    capture.add_argument("--expected-source-head", required=True)
    capture.add_argument("--include-private", action="store_true")
    point = sub.add_parser(
        "verify-point", help="verify sealed prefix and selected immutable tail"
    )
    point.add_argument("--archive-dir", type=Path, required=True)
    point.add_argument("--source-events", type=int, required=True)
    point.add_argument("--expected-source-head", required=True)
    restore_point = sub.add_parser(
        "restore-point", help="restore sealed prefix and tail to NEW SQLite"
    )
    restore_point.add_argument("--archive-dir", type=Path, required=True)
    restore_point.add_argument("--source-events", type=int, required=True)
    restore_point.add_argument("--expected-source-head", required=True)
    restore_point.add_argument("--new-db", type=Path, required=True)
    signer = sub.add_parser("seal-root", help="HMAC-sign exact PRIVATE archive root metadata")
    signer.add_argument("--archive-dir", type=Path, required=True)
    signer.add_argument("--receipt-file", type=Path, required=True)
    signer.add_argument("--key-file", type=Path, required=True)
    signer.add_argument("--source-events", type=int, required=True)
    signer.add_argument("--expected-source-head", required=True)
    auditor = sub.add_parser("verify-root", help="verify owner-HMAC and externally pinned receipt")
    auditor.add_argument("--receipt-file", type=Path, required=True)
    auditor.add_argument("--key-file", type=Path, required=True)
    auditor.add_argument("--expected-receipt-sha", required=True)
    pair = sub.add_parser("verify-copies", help="verify two explicit archive copies against owner HMAC")
    pair.add_argument("--archive-dir-a", type=Path, required=True)
    pair.add_argument("--archive-dir-b", type=Path, required=True)
    pair.add_argument("--receipt-file", type=Path, required=True)
    pair.add_argument("--key-file", type=Path, required=True)
    pair.add_argument("--expected-receipt-sha", required=True)
    transport = sub.add_parser(
        "copy-device",
        help="copy one owner-verified PRIVATE checkpoint to another VFS device",
    )
    transport.add_argument("--source-archive", type=Path, required=True)
    transport.add_argument("--destination-archive", type=Path, required=True)
    transport.add_argument("--receipt-file", type=Path, required=True)
    transport.add_argument("--key-file", type=Path, required=True)
    transport.add_argument("--expected-receipt-sha", required=True)
    transport.add_argument("--include-private", action="store_true")
    cold = sub.add_parser(
        "recover-replica",
        help="owner-HMAC verified cold restoration into a NEW PRIVATE SQLite",
    )
    cold.add_argument("--archive-dir", type=Path, required=True)
    cold.add_argument("--new-db", type=Path, required=True)
    cold.add_argument("--receipt-file", type=Path, required=True)
    cold.add_argument("--key-file", type=Path, required=True)
    cold.add_argument("--expected-receipt-sha", required=True)
    cold.add_argument("--expected-agent-id", required=True)
    cold.add_argument("--include-private", action="store_true")
    watch = sub.add_parser(
        "observe-replica",
        help="read HMAC-anchored quarantined history with immutable SQLite mode",
    )
    watch.add_argument("--db", type=Path, required=True)
    watch.add_argument("--receipt-file", type=Path, required=True)
    watch.add_argument("--key-file", type=Path, required=True)
    watch.add_argument("--expected-receipt-sha", required=True)
    watch.add_argument("--expected-agent-id", required=True)
    watch.add_argument("--include-private-metadata", action="store_true")
    watch.add_argument("--recent", type=int, default=12)
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
        elif args.operation == "restore":
            status = restore_into_new_sqlite(
                args.archive_dir, args.new_db,
                external_expected_head=args.expected_sealed_head,
            )
        elif args.operation == "capture-tail":
            if not args.include_private:
                parser.error("PRIVATE live tail requires --include-private")
            with SegmentedNoosphere(
                args.db, args.agent_id, segment_size=args.segment_size,
            ) as source:
                status = capture_live_tail(
                    source, args.archive_dir,
                    include_nonpublic=True,
                    external_expected_source_head=args.expected_source_head,
                )
        elif args.operation == "verify-point":
            status = verify_point_in_time(
                args.archive_dir, source_events=args.source_events,
                external_expected_source_head=args.expected_source_head,
            )
        elif args.operation == "restore-point":
            status = restore_point_in_time(
                args.archive_dir, args.new_db,
                source_events=args.source_events,
                external_expected_source_head=args.expected_source_head,
            )
        elif args.operation == "seal-root":
            status = sign_owner_receipt(
                args.archive_dir, args.receipt_file, args.key_file,
                source_events=args.source_events,
                external_expected_source_head=args.expected_source_head,
            )
        elif args.operation == "verify-root":
            status = verify_owner_receipt(
                args.receipt_file, args.key_file,
                independently_pinned_receipt_sha256=args.expected_receipt_sha,
            )
        elif args.operation == "verify-copies":
            status = verify_two_archive_copies(
                args.archive_dir_a, args.archive_dir_b,
                args.receipt_file, args.key_file,
                independently_pinned_receipt_sha256=args.expected_receipt_sha,
            )
        elif args.operation == "copy-device":
            if not args.include_private:
                parser.error("PRIVATE cross-device copy requires --include-private")
            status = copy_pinned_archive_to_device(
                args.source_archive, args.destination_archive,
                args.receipt_file, args.key_file,
                independently_pinned_receipt_sha256=args.expected_receipt_sha,
                include_nonpublic=True,
            )
        elif args.operation == "recover-replica":
            if not args.include_private:
                parser.error("PRIVATE replica recovery requires --include-private")
            status = recover_pinned_replica(
                args.archive_dir, args.new_db,
                args.receipt_file, args.key_file,
                independently_pinned_receipt_sha256=args.expected_receipt_sha,
                expected_agent_id=args.expected_agent_id,
                allow_private_restore=True,
            )
        else:
            with QuarantinedObserver(
                args.db, args.receipt_file, args.key_file,
                independently_pinned_receipt_sha256=args.expected_receipt_sha,
                expected_agent_id=args.expected_agent_id,
            ) as view:
                status = view.summary(
                    include_private_metadata=args.include_private_metadata,
                    max_recent=args.recent,
                )
    except (ArchiveError, NoosphereError, ReceiptError, TransferError, ObserverError) as exc:
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
