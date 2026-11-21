# R0.15 zoned-escape motor profile (issue #106)

`src/mojum/developmental_escape.py` is an **isolated, opt-in** point-room motor
policy profile. It does not change the canonical
`mojum.developmental.motor-play/1` schema, its journals or its replay
semantics. Existing Aquarium runs are untouched.

## Problem it addresses

Issue #106 describes a coarse-zone explorer that permanently skipped any action
that had once been blocked anywhere in a 0.5 m zone. Once all four directions
had one blocked outcome, the policy could select `rest` forever and look
"safe" under collision-only metrics.

## Contract

| Property | Behaviour |
| --- | --- |
| Schema | `mojum.developmental.zoned-escape/1` (independent from `/1` motor play) |
| Zone | `floor(x / 0.5) : floor(y / 0.5)` from measured proprioception only |
| Learned evidence | per-zone `{trials, blocked}` per cardinal action, Laplace-smoothed |
| `avoid` / `prefer` hints | soft ordering for the current measured decision; never delete or permanently suppress an action |
| Rest | deterministic cadence every 11th decision, hard-capped at `MAX_CONSECUTIVE_RESTS = 3` |
| Forced probe | each action is re-attempted once age `>= FORCED_EXPLORATION_PERIOD = 16` |
| Memory | at most `MAX_ZONES = 128` zones, deterministic eviction; evicted trial counts are preserved for audit |
| Budget | `MAX_OBSERVATIONS = 9_999` decisions; stale or over-budget calls fail closed |
| Snapshots | strict, immutable-input `from_snapshot` validation for replay |

## Not claimed

This is not a brain, reward, goal, navigation score, MuJoCo/physical result or
real actuation. No obstacle geometry, object identity or future sensory state
is a policy input. Passing tests here does **not** close issue #106, #55 or
#51: those require the wider comparative and release acceptance described in
the issues.

Tests: `tests/test_developmental_escape.py`.
