# R0.16 crash aftermath: read-only PRIVATE JSONL staging inventory

Status: **Draft, read-only incident response**, stacked on PR #152.
No deletions, chmod, file moves, writes, imports, network, background
sweeps, telemetry or automatic salvage of private memories.

## Why

A process that dies between `mkstemp`, file `fsync`, atomic
`os.link`, stage `os.unlink`, and parent directory `fsync`
can leave `.mojum-private-trace-<random>.jsonl` staging files.
They may be incomplete, complete but not durably synced, or still
hard-linked to an already-published output file. They contain
**PRIVATE synthetic organism frames** and may expose sensory or
learned action information, even if file permissions are 0600.
Neither the existence of a path nor an in-file SHA identifies
whether it is safe to delete, import, share, or replay.

The new module `mojum.private_trace_orphans` provides an
operator-initiated, **nonrecursive**, metadata-only audit of an
explicit existing directory on POSIX.

## How to inspect

Only inside a directory the operator owns, with mode 0700 or more
restrictive (do not indiscriminately chmod existing shared paths):

```bash
PYTHONPATH=src python -m mojum.private_trace_orphans \
  --directory /absolute/path/to/owner-only-private-export-directory \
  --expected-journal-head <independently-archived-lowercase-sha256>
```

The independent head is optional but recommended. The tool never
prints its value, a frame, full SQLite source path, decoded model
state, genome, or sensory events. Metadata includes ONLY a random
staging filename, status, byte count and hard-link count, plus
a `READ_ONLY_NO_DELETION_NO_AUTHENTICATION` declaration. An unsafe
entry or invalid/self-inconsistent JSONL triggers CLI exit code 2;
inspect failures never imply that deletion is allowed.

## Bounded and race-resistant scan

- Refuses missing/relative/symlink directory, wrong owner, or directory
  with group/other permissions. Opens parent with POSIX no-follow
  directory fd, verifies its real owner and mode.
- Never recursively traverses nested folders or guesses other
  basenames. Only exact `.mojum-private-trace-[A-Za-z0-9_-]{8,}.jsonl`.
  Maximum 512 candidates.
- Before reading any file, `stat(..., follow_symlinks=False)`, opens
  only a regular owner-owned exact 0600 file via the verified parent
  dirfd and `O_NOFOLLOW`, and compares inode/dev, size and later
  mtime. If the target changes or cannot be read safely, marks
  `unsafe_entry` without following links.
- Hard total bound 24 MiB per candidate and at most 5000 frames plus
  one footer, canonical newline-terminated UTF-8 JSON with strict
  duplicate-member and nonfinite-value refusal. Checks exact
  source frame sequence, bounded range, footer version/clock,
  v4 epoch receipts, source journal head shape and exact frame
  SHA-256.
- Even when all local bytes match, returns only
  `self_consistent_unanchored` unless a separately provided head
  matches (`self_consistent_head_matches`). A mismatched independent
  head becomes `independent_head_mismatch`; malformed data becomes
  `incomplete_or_inconsistent`.
- A stage inode with more than one hard link becomes
  `linked_manual_reconciliation`, even if content looks valid.
  The final destination may already be published under another name.
  **Never automatically unlink it.**

## Actual regression coverage

`tests/test_r016_private_orphan_inventory.py` uses real, disposable
segmented-v4 SQLite and the native replay-verified private exporter
to generate the bytes the scanner sees. It tests genuine canonical
export data with/without an independently captured head, an altered
head, incomplete and rewritten frames, duplicate JSON keys,
world-readable files, symlinks, recursively nested decoys, wrong root
permissions, hard-linked already-published outputs, and the actual
metadata-only CLI and failure exit code. CPython 3.11–3.13 on Linux.

The scan itself is a **read-only** operation. It cannot know whether a
stage had been file-fsynced when the process died, whether a directory
entry persisted through actual power loss, or whether the trusted
head came from a compromised source. It never tries to salvage a
possible private file into another journal, silently convert v2
formats, or delete the user's only copy.

## Operator decision after inventory

A human operator should corroborate an orphan against an
independently anchored original SQLite head, any *already published*
final file (careful about `hard_links>1`), and the expected export
request. If cleanup is approved, follow an explicit separately
reviewed procedure with directory fsync and secure handling of
unencrypted private data. Do NOT treat unlink as forensic secure
erasure on SSD, snapshots or flash storage.

No Android/Termux, user PRIVATE journals, owner authentication,
encrypted backups, physical power-cut, two independent nodes,
subjective experiences or production release were tested. The
upstream incompatible writable v2A/v2B ABI issue #116 remains
unresolved. Keep all source PRs as Draft for independent review.
