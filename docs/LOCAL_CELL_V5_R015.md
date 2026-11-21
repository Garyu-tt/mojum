# Local Cell v5: bounded retention and bounded rest (R0.15)

Opt-in synthetic motor policy for the CPU fixture aquarium. It is **not** a
neural brain, does not perform MuJoCo physics, has no reward, pain, valence,
goal or obstacle-geometry input, and grants no physical actuation.

## Why a new version

`local_cell_v4` added explicit absolute ticks and epoch markers, but it kept
the selection rule of v3, which can stop moving for an unbounded time:

- `rest` is never blocked, so it always remains eligible;
- `rest` sits inside the rotated action order, so it wins on one of every five
  shifts even in a completely fresh cell;
- once all four 4 cm moves have one measured block in a cell, only `rest`
  remains — forever.

That is the canonical rest-collapse pathology tracked in
[issue #106](https://github.com/Garyu-tt/mojum/issues/106): blocked counts can
fall while the organism barely moves. Changing `/4` would reinterpret persisted
evidence, so the anti-stall rule ships as a separate schema.

## Policy contract

- `SCHEMA = "mojum.developmental.local-cell/5"`.
- `LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/5"`.
- Intent `source = "bounded_goal_free_local_cell_motor_play_v5"`.
- Manifest selector: `exploration_mode=goal_free`,
  `developmental_schema=mojum.developmental.local-cell/5`,
  `developmental_policy=local_cell_v5`, plus the v4 epoch keys
  (`epoch_transition_schema`, `epoch_ticks`, `proprioception_schema`,
  `max_absolute_ticks`).
- CLI: `--motor-mode local_cell_v5`.

### Selection rule

1. A move (`x+`, `y+`, `x-`, `y-`) with no measured block in the current cell
   always outranks rest.
2. Otherwise, at most `MAX_REST_STREAK = 3` consecutive rests are allowed.
3. After that the policy re-probes the least-attempted direction in the current
   rotation order. Ties break by rotation position, so replay is deterministic.
4. Effect: the longest possible rest run is three decisions, at any cell count.

`rest` can never be recorded as a blocked outcome, and repeated blocks of the
same direction are legitimate evidence, so `blocked <= attempts` rather than
v4's one-block invariant.

### Retention and time

Same as v4: at most `MAX_CELLS = 512` live cells, least-recently-observed cell
evicted with a stable key tie-break, evictions and forgotten observations
reported separately as a data budget rather than a negative signal. Ticks are
absolute (`MAX_ABSOLUTE_TICKS = 99_990`) and epoch markers are committed by the
Aquarium exactly as for v4.

## Snapshot

Strict envelope, all keys mandatory:

```
schema, cell_size_m, last_tick, last_epoch_index, updates, records,
last_seen_at, evictions, forgotten_observations, rest_streak, forced_probes
```

Validation fails closed on: cross-version schema, wrong cell size,
non-integer or boolean-as-integer fields, `updates != last_tick + 1`,
`last_epoch_index != last_tick // 9999`, unknown cell keys or actions,
malformed counters, blocked rest, lost or double-counted observations,
forgotten evidence without eviction, `rest_streak` outside `0..3`,
`forced_probes` outside `0..updates`, and any extra or missing key.

## Measurement

`mojum.cell_policy_v5.summarize_transcript(transcript)` accepts the
chronological `(intent, outcome)` pairs of one run and returns ticks, attempts,
rest steps, blocked moves, successful moves, longest rest streak, idle
fraction, distinct cells and forced probes. A run with few collisions and a
large idle fraction is a rest collapse, not progress.

## Limits

- v5 does not migrate existing `/2`, `/3` or `/4` journals; it refuses to open
  them and never rewrites their bytes.
- The journal keeps the 100,000-event budget and 99,990 absolute-tick ceiling.
- The policy is synthetic and local-only. It makes no claim of navigation
  skill, coverage of a real room, or transfer to a physical body.
