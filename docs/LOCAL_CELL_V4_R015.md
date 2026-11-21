# Local Cell v4: bounded retention and bounded rest (R0.15)

Opt-in synthetic motor policy for the CPU fixture aquarium. It is **not** a
neural brain, does not perform MuJoCo physics, has no reward, pain, valence,
goal or obstacle-geometry input, and grants no physical actuation.

## Why a new version

`local_cell_v3` (schema `mojum.developmental.local-cell/3`) introduced
deterministic bounded retention, but it can stop moving for an unbounded time:

- `rest` is never blocked, so it always remains an eligible choice;
- `rest` sits inside the rotated action order, so it wins on one of every five
  shifts even in a completely fresh cell;
- once all four 4 cm moves have one measured block in a cell, only `rest`
  remains — forever.

That is the canonical rest-collapse pathology tracked in
[issue #106](https://github.com/Garyu-tt/mojum/issues/106): blocked counts can
fall while the organism barely moves. Fixing it inside `/3` would reinterpret
persisted evidence, so v4 is a separate schema.

## Policy contract

- `SCHEMA = "mojum.developmental.local-cell/4"`.
- `LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/4"`.
- Intent `source = "bounded_goal_free_local_cell_motor_play_v4"`.
- Manifest selector: `exploration_mode=goal_free`,
  `developmental_schema=mojum.developmental.local-cell/4`,
  `developmental_policy=local_cell_v4`.
- CLI: `--motor-mode local_cell_v4`.

### Selection rule

1. A move (`x+`, `y+`, `x-`, `y-`) with no measured block in the current cell
   always outranks rest.
2. Otherwise, at most `MAX_REST_STREAK = 3` consecutive rests are allowed.
3. After that the policy re-probes the least-attempted direction in the current
   rotation order. Ties break by rotation position, so replay is deterministic.
4. Rest is only chosen when every move in the cell has blocked evidence and
   the rest streak is below the cap, or when no move evidence exists (which
   cannot happen, because untried moves win first).

`rest` can never be recorded as a blocked outcome, and repeated blocks of the
same direction are legitimate evidence, so `blocked <= attempts` rather than
v3's one-block invariant.

### Retention

Identical to v3: at most `MAX_CELLS = 512` live cells, least-recently-observed
cell evicted on demand with a stable key tie-break. Eviction counts and
forgotten observations are reported separately and are a data budget, not a
negative experience.

## Snapshot

Strict envelope, all keys mandatory:

```
schema, cell_size_m, last_tick, updates, records, last_seen_at,
evictions, forgotten_observations, rest_streak, forced_probes
```

Validation fails closed on: cross-version schema, wrong cell size, non-integer
or boolean-as-integer fields, `updates != last_tick + 1`, unknown cell keys or
actions, malformed counters, blocked rest, lost or double-counted
observations, forgotten evidence without eviction, `rest_streak` outside
`0..MAX_REST_STREAK`, `forced_probes` outside `0..updates`, and any extra or
missing key.

## Measurement

`mojum.cell_policy_v4.summarize_transcript(transcript)` accepts the
chronological `(intent, outcome)` pairs of one run and returns:

| field | meaning |
| --- | --- |
| `ticks` | decisions recorded |
| `attempts` | non-rest decisions |
| `rest_steps` | rest decisions |
| `blocked` | measured blocked moves |
| `successful_moves` | attempted moves that were not blocked |
| `longest_rest_streak` | longest consecutive rest run |
| `idle_fraction` | `rest_steps / ticks`, `0.0` for an empty run |
| `distinct_cells` | distinct 4 cm cells mentioned by intents |
| `forced_probes` | anti-stall re-probes |

Blocked counts alone are not a movement gate: a run with few collisions and a
large idle fraction is a rest collapse, not progress.

## Limits

- The journal still uses the 10,000-event replay budget; v4 does not lift the
  long-run ceiling tracked in [#129](https://github.com/Garyu-tt/mojum/issues/129).
- v4 does not migrate existing `/2` and `/3` journals. It refuses to open them
  and never rewrites their bytes.
- The policy is synthetic and local-only. It makes no claim of navigation
  skill, coverage of a real room, or transfer to a physical body.
- Zero-tick replays and cold replay of a v4 journal are covered by tests, but
  no v4 history has been promoted to a release default.
