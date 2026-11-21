# Mojum R0.7 — crash-gap audit before any recovery

The canonical Aquarium journal (PRIVATE Noosphere SQLite) and the local
authority registry are **different SQLite databases**. Their respective
commits cannot be treated as one atomic transaction. A machine crash
after the Noosphere append but before the authority registry's updated
head has committed can leave the journal one event ahead.

**An extra event is NOT proof of an interrupted authorized write.** A
compromised writer-service UID can append the very same number of events
outside the guarded gateway. Safe unattended recovery requires a durable,
independently stored *pre-commit operation intent* with expected event
identity and a reviewed trust boundary; the existing PR #85 stack does
not have such a receipt.

This change therefore implements **forensic classification only**. It
deliberately never modifies the registry head, writer, epoch, generation,
pending owner or history and never replaces a live source journal.

## Offline invocation

On a trusted local host under the service owner's account, with the
writer process quiesced (for example, confirmed stopped by systemd),
supply the preincident values of the writer/epoch/generation and the
same genome, graph and neural mode used by the actual synthetic Aquarium:

```bash
PYTHONPATH=src python -m mojum.recovery_audit \
  --journal /var/lib/mojum/private/dante.sqlite3 \
  --registry /var/lib/mojum/private/authority.sqlite3 \
  --organism-id dante-fixture-001 \
  --writer node-a --epoch 1 --generation 123 \
  --genome examples/genesis_seed.json \
  --graph examples/first_loop.json --neural-mode shadow
```

The test adapter does not attempt to infer missing seed/graph/room/clock
settings from the journal. For nondefault Room, start, clock or other
supported Aquarium constructor settings, use the Python
`inspect_recovery(..., aquarium_options={...})` API explicitly. An
incorrect configuration fails validation; guessing would be unsafe.

The inspector takes a cooperative `BEGIN IMMEDIATE` reservation on
the local authority database, verifies the claimed writer/epoch/
generation, and makes a **consistent SQLite backup** of the journal
including WAL contents through a read-only connection. That copied
database is staged in an automatically deleted private scratch directory,
then fully replayed using the original deterministic Aquarium logic.
The source is re-read after copying to detect ordinary direct writes
that occurred during inspection.

Only tick, event counts, journal heads, registry phase/epoch/generation,
classification and the common-ancestor flag are returned. Private event
payloads, acquired-gene values, sensory frames and replay contents never
appear in JSON output.

## Decisions

| Code | Classification | Meaning |
| --- | --- | --- |
| 0 | IN_SYNC | Journal agrees exactly with ACTIVE registry at audit instant |
| 3 | ONE_EXTRA_EVENT_AMBIGUOUS_STOP | Journal extends registry by exactly one event |
| 3 | MULTIPLE_EXTRA_EVENTS_STOP | Journal has two or more unanchored appended events |
| 3 | JOURNAL_ROLLBACK_OR_FORK_STOP | Journal shorter than registry or different at same count |
| 3 | JOURNAL_FORK_STOP | Journal has more events but trusted registry head is not its exact prefix |
| 3 | JOURNAL_TICK_REGRESSION_STOP | Longer journal but tick regressed |
| 3 | AUTHORITY_FROZEN_STOP | FROZEN transfer state must not be treated as active |
| 3 | JOURNAL_CHANGED_DURING_AUDIT_STOP | Original journal advanced between backup and final head read |
| 3 | JOURNAL_INVALID_STOP | Copied journal cannot be replayed and verified |
| 3 | SOURCE_REPLACED_STOP, REGISTRY_REPLACED_STOP | Local pathname changed during inspection |
| 2 | Invalid inputs or unavailable stores | CLI argument/IO error, no repair |

**Every single result contains
`auto_repair_allowed: false`.** Even a fully valid one-extra-event
journal is ambiguous without a **pre-existing, independently trusted
intent record** created before its append; silently setting the registry
head to the newest event is unsafe.

A classification is a snapshot of local evidence at one point in time.
The inspector's registry reservation blocks *cooperating* writers but
does not block a process which bypasses LocalAuthority. The final
source-read check can detect some concurrent external writes; it
cannot eliminate time-of-check/time-of-use races after the report.

A fully replayed hash chain is not a digital signature and cannot
authenticate its creator. The registry's own history must be trusted
independently and backed up against tampering/rollback. There is no
distributed quorum, signed epoch, network fence or authority promotion
here. Filesystem ACLs, raw database access by owner/root, WAL durability
and the cross-file crash gap still need separate hardening.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_recovery_audit_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

The 13 regression tests create actual SQLite Aquarium/authority files,
simulate a post-Noosphere/pre-registry commit failure, distinguish
unauthorized acquisition with unchanged tick, two extra events, an
alternative valid bootstrap fork, authority rollback, corrupt source,
a direct writer during backup, FROZEN transfer, missing files, stale
writer epoch/generation and CLI exit behavior. The audit never moves
the authority head or publishes the PRIVATE snapshot.

## Next reviewed protocol step

Before offering *any* automated or operator-confirmed repair, implement
durable prepare/commit intent logging **prior to the Noosphere append**,
pin the exact expected event hash and writer epoch/generation, and verify
that the journal advanced by **exactly that single predeclared event**
after a crash. Reconcile through one trusted single-writer service with
manual override only under separately authenticated incident procedure.
Never extrapolate this into safe multi-node failover.
