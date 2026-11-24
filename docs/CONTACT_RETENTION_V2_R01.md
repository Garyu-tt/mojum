# Contact association retention v2 (R0.1)

## Status and boundary

This is an opt-in, stdlib-only aggregate retention primitive for explicitly supplied
`PUBLIC_SYNTHETIC` measurements. It is deliberately separate from
`sensory_play.ContactAssociations` and its persisted association/1 schema.
It does not rewrite old snapshots, read private journals, access hardware, infer
object semantics, provide reward, or wire itself into Aquarium.

## Contract

- Schema: `mojum.sensory.contact-retention/2`.
- Input: bounded UTF-8 cue ID, integer milli-value in [0, 1000], strictly
  increasing logical tick, and explicit PUBLIC_SYNTHETIC provenance.
- TTL expiry uses logical ticks only; an entry expires when age is greater than
  the configured retention window.
- Capacity eviction is deterministic LRU by last observation tick, then cue ID.
- Per-cue counters saturate at a caller-selected bound; integer-only mean updates
  keep the aggregate bounded and reproducible.
- Snapshots are detached canonical JSON data with SHA-256 consistency digest.
  The digest is an integrity witness, not authentication or encryption.

## Resource bounds

Defaults: 128 cue aggregates, 10,000 logical ticks of retention, and 1,000,000
observations per cue. Hard upper bounds: 4,096 cue aggregates, 10,000,000 TTL
ticks, and 2^31-1 observations per cue. Memory is O(max_cues), independent of
total historical observations.

## Not included

No SQLite adapter, cold replay integration, HMAC/signature, cross-process
coordination, private bootstrap compatibility, or migration. The 4000→10050
end-to-end issue acceptance remains open until separately implemented and
independently reviewed. Do not interpret this report as a real sensor or
production retention guarantee.
