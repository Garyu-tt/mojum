# Local Cell/3-escape causal outcome tail R01

This opt-in snapshot hardening adds snapshot_version 2, a history_complete flag, and a bounded ordered outcome_tail containing the latest four measured decisions. The flag is true exactly when all four bounded tail slots are present; truncating a complete tail is rejected. Legacy snapshots with zero terminal streaks remain replayable as incomplete until four new observations accumulate. Each entry records the logical tick, quantized cell, selected action and measured blocked result.

The loader checks contiguous tail ticks, exact bounded length, action/cell validity, rest semantics, agreement with retained aggregate counts, and exact agreement between the terminal tail suffix and the persisted rest/blocked-probe streak counters. This rejects aggregate snapshots that could describe an interleaved history such as rest, move, rest while claiming two terminal rests.

Legacy snapshots without chronology are accepted only when both terminal streak counters are zero. A legacy snapshot claiming a non-zero terminal streak is rejected because its order cannot be independently established. No history is invented during migration. The tail is bounded to four entries and remains inside the existing 512-cell LRU; no network, storage, or new dependency is introduced.

This is internal consistency validation, not cryptographic attestation: a party able to rewrite the entire snapshot can fabricate mutually consistent fields. Canonical journal authentication and provenance remain separate gates.
