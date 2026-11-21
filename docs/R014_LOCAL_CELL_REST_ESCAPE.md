# Local Cell/3-escape: bounded rest escape (isolated R0.14 profile)

Status: **opt-in, isolated module.** This profile is *not* wired into the
Aquarium manifest, SQLite wire format, CLI motor modes or existing journals.
Local Cell/2 and Local Cell/3 history is never reinterpreted.

## Defect (issue #106)

`CellExplorer` (`Local Cell/2`) and `CellExplorerV3` (`Local Cell/3`) select
the first rotation candidate whose `blocked` counter is zero and otherwise
fall back to `"rest"`. Because `rest` can never block in the point-room
fixture, a cell whose four movement actions were each blocked once becomes a
**terminal rest state**:

```
x+ blocked, y+ blocked, x- blocked, y- blocked  ->  rest, rest, rest, ...
```

Collision-only metrics then look "safe" while mobility is gone. A silent
rest-collapse must fail a gate even when blocked counts stop growing.

## Rule in `mojum.developmental.local-cell/3-escape`

Inputs remain only the agent's own `Proprioception` (tick, world revision,
position, heartbeat pulse) and its own *measured* blocked outcomes. No room
geometry, obstacle oracle, goal, reward, pain/valence, neural brain or
hardware actuation.

1. Per-4cm-cell evidence and v3 bounded LRU retention (512 cells) are kept.
2. While at least one movement action in the cell has no blocked evidence, the
   innate rotation is preserved, including bounded rest.
3. If every movement action in the cell has blocked evidence, the policy
   re-probes its own blocked evidence instead of resting terminally.
4. After `REPROBE_BLOCKED_BEFORE_REST = 4` consecutive failed probes it may
   rest for at most `MAX_CONSECUTIVE_RESTS = 3` ticks, then probes again.
   Rest remains a right, never a terminal outcome.

Re-probing can measure the same action blocked more than once, so
`blocked_outcomes` above one are legitimate in this schema. That semantic
difference is why the learning schema is versioned
(`mojum.developmental.local-cell.outcome/3-escape`) rather than reusing v3.

## Bounded synthetic comparison

`python -m mojum.cell_policy_v3_escape --ticks 60` runs `Local Cell/3` and the
escape profile over identical rooms, starting states, pulse streams and virtual
time (deterministic point-room fixture, no MuJoCo/physical contact):

| scenario | policy | rest | moves | blocked | idle | longest rest streak | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sealed 4cm cell | v3 | 56 | 4 | 4 | 0.933 | 56 | `REST_COLLAPSE_SUSPECTED` |
| sealed 4cm cell | v3-escape | 12 | 48 | 48 | 0.200 | 1 | `NO_MEASURED_DISPLACEMENT` |

The open and wall-corner scenarios are also reported. In a sealed cell neither
policy can move; the escape profile reports that honestly (`NO_MEASURED_DISPLACEMENT`)
instead of hiding behind rest. If a direction is free later, re-probing finds
it. These are bounded synthetic measurements, **not** navigation scores,
intelligence/emotion claims, release evidence or real actuation.

## Integration boundary

Promoting this rule into the canonical Aquarium requires a versioned
motor/manifest wire entry plus replay handling in a separate change. This
module deliberately touches no manifest, SQLite, CLI mode or peer branch, so
existing private journals and the P0 wire-compatibility gate (#116) are
unaffected. Regression tests live in
`tests/test_cell_policy_v3_escape.py`.
