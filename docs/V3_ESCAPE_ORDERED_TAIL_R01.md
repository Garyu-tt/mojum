# Local Cell/3-escape chronological outcome tail (issue #646)

This opt-in bounded CPU-first extension of the existing Local Cell/3-escape
motor snapshot fixes the claim that aggregate counts prove *consecutive*
terminal rests or blocked probes. It does not authenticate who wrote a snapshot:
independent replay of authenticated journals remains necessary.

## Wire format, bounds and observations

The original motor schema and learning-event schema remain unchanged.
Snapshots emitted after new measured outcomes append three fields:

- ordered_tail_schema: mojum.developmental.local-cell.outcome-tail/1
- ordered_from_tick: first tick eligible for ordered proof. Zero for a fresh
  policy; old_snapshot.updates when restored from a legacy snapshot.
- ordered_outcomes: up to FOUR contiguous latest events, each with EXACT
  keys: tick (integer), cell (zone), action, blocked (boolean).

Events are recorded only by observe(), after validation of canonical
proprioception, intent and measured outcome. Additional storage is O(4),
leaving the 512-cell LRU bounded. No room geometry, goal, reward, private
Noosphere memory, network or hardware actuation is introduced.

The verifier checks exact schema, keys and types, contiguous ticks ending
at last_tick, retained measured counts and blocked outcomes, cell last-seen
timestamps, and the terminal consecutive rest/blocked-probe run IN ORDER.
When a full recorded history fits in four ticks, exact totals must also
reconcile. Older aggregate and retention validators still run first.

## Compatibility and strict claims

CellExplorerV3Escape.from_snapshot(state) keeps accepting old ten-field
snapshots for historic replay. It never fabricates missing ordered events.
A legacy-restored policy emits the original wire form until its next
observe(); subsequent events start a tail with ordered_from_tick at the
historic boundary.

Use the explicit strict API to claim actual chronological consistency:

    CellExplorerV3Escape.from_snapshot(state, require_ordered_tail=True)

The strict API rejects old snapshots that contain history but no tail,
and rejects migrated tails too short to prove the claimed terminal streak.
Non-strict compatibility loading never means causal attestation. Old
journals must not be silently relabeled as proven. These checks detect
snapshot inconsistency but do not provide a cryptographic integrity proof.

## Focused QA

    PYTHONPATH=src python -m unittest discover -s tests -p test_v3e_ordered_tail_646.py -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_cell_policy_v3_escape.py -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_v3e_terminal_streak_evidence.py -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_aquarium_v3_escape_mode.py -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_aquarium_wire_v3_escape.py -v

New regressions reject rest,move,rest and blocked,success,blocked as
two-terminal-outcome streaks. They also cover cross-cell authentic runs,
legacy migration, partial/forged evidence, and 512-cell LRU eviction.

Independent exact-head Aquarium/Motor/Mesh Python 3.11-3.13 QA and review
remain mandatory before merge. No claim of organism safety or real hardware.
