# Local Cell/3-Escape snapshot chronology, R0.1

## Snapshot format

New snapshots retain the existing profile schema and add `snapshot_version: 2`,
`history_complete`, and `outcome_tail`. The tail is a maximum of four ordered
records. Each record contains exactly `tick`, `cell`, `action`, and
`blocked`. Four entries cover the largest supported terminal blocked-probe
streak; the three-entry rest limit is covered as well.

Restore requires consecutive ticks ending at `last_tick`, known retained
cells/actions, valid booleans, no blocked rest, and tail counts bounded by the
per-cell aggregate counters. It recomputes the terminal rest/blocked streak
from the tail in reverse order and requires exact equality with the stored
counters. This detects interleaved histories such as rest/move/rest and
blocked/success/blocked even when their aggregate totals look plausible.

## Legacy compatibility

A legacy snapshot with no chronology remains loadable only when both terminal
streak counters are zero. It is migrated in memory to version 2 with
`history_complete: false` and an empty tail. New observations extend the
tail; after four new contiguous observations, the tail is complete. A legacy
snapshot claiming a nonzero terminal streak is rejected because that claim
cannot be causally attested from its aggregates. No historical record is
silently reinterpreted.

## Limits

The tail is consistency evidence, not a cryptographic signature or proof that
the caller's supplied snapshot originated from a trusted process. Aggregate
records remain bounded by the existing 512-cell LRU. The change is stdlib-only,
offline, and does not alter canonical journals or runtime authority.
