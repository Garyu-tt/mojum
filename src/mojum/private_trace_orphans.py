"""Read-only PRIVATE JSONL orphan inventory after an interrupted export.

No deletes, renames, chmod, quarantine moves, journal writes, network or
automatic imports. Frame SHA from the *same* file is only self-consistency,
NEVER a signature, durability proof or permission to discard originals.

The scan requires an explicit POSIX owner-only directory. It uses a
directory fd, no-follow opens, inode equality and bounded reads to avoid
symlink and replace-between-list-and-open disclosure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .aquarium_trace_export import MAX_TRACE_BYTES, MAX_FRAMES, MAX_JSONL_CHARS

STAGE_PATTERN = re.compile(r"\.mojum-private-trace-[A-Za-z0-9_-]{8,}\.jsonl\Z")
HEAD_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
INTEGRITY_SCHEMA = "mojum.aquarium.trace-frames-sha256/1"
EPOCH_SCHEMA = "mojum.aquarium.epoch-transition/1"

# A bounded scanner should never deserialize arbitrary multi-gigabyte data.
MAX_ITEMS = 512


class PrivateOrphanAuditError(ValueError):
    """Unsafe scanner configuration; do not descend or attempt cleanup."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(text):
    raise ValueError("nonfinite JSON value")


def _decode_canonical_line(raw):
    if (not raw.endswith(b"\n") or raw.startswith(b"\xef\xbb\xbf")
            or len(raw) > MAX_JSONL_CHARS * 4 + 1):
        raise ValueError("noncanonical frame/metadata line")
    parsed = json.loads(
        raw[:-1].decode("utf-8"),
        object_pairs_hook=_unique_object, parse_constant=_reject_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    if ((json.dumps(parsed, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            != raw):
        raise ValueError("noncanonical JSON encoding")
    return parsed


def _internally_consistent(data, expected_head):
    if not data or len(data) > MAX_TRACE_BYTES or not data.endswith(b"\n"):
        return "incomplete_or_inconsistent"
    lines = data.splitlines(keepends=True)
    if not 2 <= len(lines) <= MAX_FRAMES + 1:
        return "incomplete_or_inconsistent"
    try:
        frames = [_decode_canonical_line(line) for line in lines[:-1]]
        footer = _decode_canonical_line(lines[-1])
        if footer.get("schema") not in (
            "mojum.aquarium.checkpoint/1",
            "mojum.aquarium.checkpoint/2",
        ):
            raise ValueError("unsupported checkpoint wire")
        source_head = footer.get("journal_head")
        if type(source_head) is not str or not HEAD_PATTERN.fullmatch(source_head):
            raise ValueError("not a journal-head SHA")
        first = frames[0].get("tick")
        if type(first) is not int or first < 0:
            raise ValueError("invalid first absolute tick")
        for i, frame in enumerate(frames):
            if frame.get("schema") != "mojum.aquarium.frame/1":
                raise ValueError("unexpected frame schema")
            if type(frame.get("tick")) is not int or frame["tick"] != first + i:
                raise ValueError("missing or reordered frames")
        frame_bytes = b"".join(lines[:-1])
        integrity = footer.get("trace_integrity")
        if not isinstance(integrity, dict) or (
            integrity.get("schema") != INTEGRITY_SCHEMA
            or type(integrity.get("byte_length")) is not int
            or integrity["byte_length"] != len(frame_bytes)
            or type(integrity.get("frame_count")) is not int
            or integrity["frame_count"] != len(frames)
            or integrity.get("sha256") != hashlib.sha256(frame_bytes).hexdigest()
        ):
            raise ValueError("invalid frame checksum")
        rng = footer.get("trace_range")
        if not isinstance(rng, dict) or (
            type(rng.get("start_tick")) is not int or rng["start_tick"] != first
            or type(rng.get("next_tick")) is not int
            or rng["next_tick"] != first + len(frames)
            or type(rng.get("source_total_ticks")) is not int
            or rng["source_total_ticks"] < rng["next_tick"]
            or type(rng.get("has_more")) is not bool
            or rng["has_more"] != (rng["next_tick"] < rng["source_total_ticks"])
            or type(footer.get("tick")) is not int
            or footer["tick"] != rng["source_total_ticks"]
            or type(footer.get("stored_events")) is not int
            or footer["stored_events"] < footer["tick"] + 1
        ):
            raise ValueError("invalid snapshot segment range")
        if footer["schema"] == "mojum.aquarium.checkpoint/2":
            if (footer.get("policy_schema") != "mojum.developmental.local-cell/4"
                    or type(footer.get("epoch_index")) is not int
                    or footer["epoch_index"] != footer["tick"] // 9999
                    or type(footer.get("epoch_tick")) is not int
                    or footer["epoch_tick"] != footer["tick"] % 9999
                    or not isinstance(footer.get("epoch_boundaries"), list)):
                raise ValueError("invalid v4 epoch metadata")
            expected = list(range(
                (first // 9999 + 1) * 9999 if first % 9999 else first,
                rng["next_tick"], 9999
            ))
            expected = [x for x in expected if x > 0]
            actual = footer["epoch_boundaries"]
            if len(actual) != len(expected):
                raise ValueError("epoch boundary count mismatch")
            for item, tick in zip(actual, expected):
                if not isinstance(item, dict) or item != {
                    "schema": EPOCH_SCHEMA, "absolute_tick": tick,
                    "epoch_index": tick // 9999,
                }:
                    raise ValueError("missing epoch boundary")
            # A self-consistent SHA cannot make a forged epoch frame a
            # coherent v4 observation. Apply the SAME read-only temporal
            # contract as the offline viewer before calling an orphan
            # internally consistent, even with a matching external head.
            for frame in frames:
                tick = frame["tick"]
                index, offset = divmod(tick, 9999)
                dev = frame.get("developmental")
                if not isinstance(dev, dict) or dev.get("schema") != (
                        "mojum.developmental.local-cell/4"):
                    raise ValueError("frame policy mismatch")
                observation = dev.get("observation")
                intent = dev.get("intent")
                learning = dev.get("learning")
                interoception = frame.get("interoception")
                heartbeat = (interoception.get("heartbeat")
                             if isinstance(interoception, dict) else None)
                if (tick >= 99999
                        or not isinstance(observation, dict)
                        or not isinstance(intent, dict)
                        or not isinstance(learning, dict)
                        or not isinstance(heartbeat, dict)
                        or observation.get("schema")
                        != "mojum.developmental.proprioception/2"
                        or learning.get("schema")
                        != "mojum.developmental.local-cell.outcome/4"
                        or type(observation.get("absolute_tick")) is not int
                        or observation["absolute_tick"] != tick
                        or type(observation.get("epoch_index")) is not int
                        or observation["epoch_index"] != index
                        or type(observation.get("epoch_tick")) is not int
                        or observation["epoch_tick"] != offset
                        or type(intent.get("tick")) is not int
                        or intent["tick"] != tick
                        or type(intent.get("epoch_index")) is not int
                        or intent["epoch_index"] != index
                        or type(intent.get("epoch_tick")) is not int
                        or intent["epoch_tick"] != offset
                        or type(learning.get("epoch_index")) is not int
                        or learning["epoch_index"] != index
                        or type(learning.get("epoch_tick")) is not int
                        or learning["epoch_tick"] != offset
                        or type(learning.get("total_updates")) is not int
                        or learning["total_updates"] != tick + 1
                        or observation.get("heartbeat_pulse") != heartbeat.get("pulse")):
                    raise ValueError("incoherent v4 epoch/sense/motor/learning frame")
        elif "policy_schema" in footer:
            declared = footer["policy_schema"]
            if not isinstance(declared, str):
                raise ValueError("invalid legacy policy identity")
            for frame in frames:
                developmental = frame.get("developmental")
                actual = "scripted" if developmental is None else (
                    developmental.get("schema")
                    if isinstance(developmental, dict) else None
                )
                if actual != declared:
                    raise ValueError("mixed policy frames")
    except (ValueError, UnicodeError, TypeError, KeyError, OverflowError):
        return "incomplete_or_inconsistent"

    if expected_head is None:
        return "self_consistent_unanchored"
    return ("self_consistent_head_matches" if source_head == expected_head
            else "independent_head_mismatch")


def inspect_private_trace_orphans(directory, *, expected_journal_head=None):
    """Return metadata ONLY for exact staging-file names; never alter content.

    Directory MUST be a single explicit local POSIX owner-only (0700 or
    stricter) directory; never recursively scan or resolve symlink children.
    Refuses unsafe directory permissions/ownership before opening a file.
    This is a forensic aid, not an authenticity, deletion or rollback oracle.
    """
    if os.name != "posix" or not hasattr(os, "getuid"):
        raise PrivateOrphanAuditError("POSIX owner-only directory scanning is required")
    if expected_journal_head is not None and (
            type(expected_journal_head) is not str
            or not HEAD_PATTERN.fullmatch(expected_journal_head)):
        raise PrivateOrphanAuditError("expected head must be independently captured SHA-256")
    path = Path(directory)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise PrivateOrphanAuditError("explicit existing nonsymlink absolute directory required")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(str(path), flags)
    try:
        root = os.fstat(fd)
        if (not stat.S_ISDIR(root.st_mode) or root.st_uid != os.getuid()
                or (stat.S_IMODE(root.st_mode) & 0o077)):
            raise PrivateOrphanAuditError("directory must be owner-only and owned by caller")
        names = sorted(n for n in os.listdir(fd) if STAGE_PATTERN.fullmatch(n))
        if len(names) > MAX_ITEMS:
            raise PrivateOrphanAuditError("too many private stage candidates; refuse bulk scan")
        records = []
        for name in names:
            # The name is a random tempfile basename. Never emit a frame,
            # source path, genome, memory state or journal hash in stdout.
            record = {"name": name, "status": "unsafe_entry"}
            try:
                original = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(original.st_mode)
                        or original.st_uid != os.getuid()
                        or stat.S_IMODE(original.st_mode) != 0o600
                        or original.st_size > MAX_TRACE_BYTES
                        or original.st_size < 0):
                    records.append(record)
                    continue
                rflags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                rflags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
                candidate = os.open(name, rflags, dir_fd=fd)
                try:
                    opened = os.fstat(candidate)
                    if ((opened.st_dev, opened.st_ino) !=
                            (original.st_dev, original.st_ino)
                            or not stat.S_ISREG(opened.st_mode)
                            or opened.st_uid != os.getuid()
                            or stat.S_IMODE(opened.st_mode) != 0o600
                            or opened.st_size != original.st_size):
                        records.append(record)
                        continue
                    chunks = []
                    remaining = opened.st_size
                    while remaining:
                        chunk = os.read(candidate, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    after = os.fstat(candidate)
                    if (remaining or (after.st_size, after.st_mtime_ns) !=
                            (opened.st_size, opened.st_mtime_ns)):
                        records.append(record)
                        continue
                    status = _internally_consistent(
                        b"".join(chunks), expected_journal_head
                    )
                    if after.st_nlink > 1:
                        # The same bytes may ALREADY be visible at an output
                        # pathname. Never infer it is safe to unlink a stage.
                        status = "linked_manual_reconciliation"
                    record.update({
                        "status": status, "bytes": opened.st_size,
                        "hard_links": after.st_nlink,
                    })
                finally:
                    os.close(candidate)
            except OSError:
                # Fail closed for any concurrent name replacement/removal.
                pass
            records.append(record)
        return {"schema": "mojum.private-trace.orphan-audit/1",
                "action": "READ_ONLY_NO_DELETION_NO_AUTHENTICATION",
                "orphan_candidates": len(records), "files": records}
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only inspection of interrupted PRIVATE JSONL export staging files"
    )
    parser.add_argument("--directory", type=Path, required=True,
                        help="absolute owner-only (0700) folder; no recursive scan")
    parser.add_argument("--expected-journal-head",
                        help="optional separately anchored original source head")
    args = parser.parse_args(argv)
    result = inspect_private_trace_orphans(
        args.directory, expected_journal_head=args.expected_journal_head
    )
    # Metadata only; never print PRIVATE frame data, genome, JSON body or head.
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if any(row["status"] in (
        "unsafe_entry", "incomplete_or_inconsistent",
        "independent_head_mismatch", "linked_manual_reconciliation",
    ) for row in result["files"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
