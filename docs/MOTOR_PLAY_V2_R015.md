# R0.15 bounded-attempt motor play v2 (isolated profile)

Status: **evaluation profile, not Aquarium ABI.** Nothing in this document
claims MuJoCo contact, physical actuation, learning of a goal, or intelligent
navigation. It is the isolated-profile response to
[issue #106](../README.md) ("prevent coarse-zone learned motor inhibition from
collapsing into permanent rest").

## Why a second schema

`src/mojum/developmental.py` ships `MotorPlay` with schema
`mojum.developmental.motor-play/1`. Its `choose(tick, avoid=(), prefer=())`
signature lets a caller permanently exclude directions based on outcomes
collected anywhere in a coarse zone. Existing journals pin that schema; changing
its semantics in place would silently reinterpret private history and could
break deterministic replay.

`src/mojum/motor_play_v2.py` therefore defines a separate profile with schema
`mojum.developmental.motor-play/2` and profile name `bounded_attempt_v2`. The
module is deliberately not imported by `mojum.aquarium`; a subprocess test
asserts that importing the canonical Aquarium does not load it.

## Behaviour

| Rule | Value |
| --- | --- |
| Step | 0.125 m virtual cardinal step (unchanged) |
| Actions | `east`, `north`, `west`, `south`, `rest` |
| Rest cap | 3 consecutive recorded rests (`MAX_CONSECUTIVE_REST`) |
| Exploration quota | 2 trials per direction before scores dominate |
| Scheduled rest | one at `tick % 13 == 12` |
| Learning input | own measured `blocked` outcome only |
| External `avoid` / `prefer` | not accepted at all |

Decision ladder, first match wins:

1. Rest streak at cap -> forced least-tried move (`rest_cap_forced_move`).
2. First four ticks -> one cardinal probe each (`initial_cardinal_probe`).
3. Any direction below the quota -> least-tried direction
   (`exploration_quota`).
4. `tick % 13 == 12` -> one scheduled rest (`scheduled_rest`).
5. Otherwise -> best Laplace-smoothed success rate, ties broken by a SHA-256
   digest of the policy's own counters (`measured_success`).

The hash tie-break matters. A fixed cardinal-order tie-break produced the
sequence `east, north, west, south`, which cancels displacement and leaves the
agent in a single 0.5 m zone. The hash is derived only from the observation
count, action name and that action's own measured counts, so no room geometry,
object identity, goal, reward or future measurement enters the policy.

## Integrity invariants

* `rest_streak` can never exceed `MAX_CONSECUTIVE_REST`.
* `observe` rejects a rest outcome once the cap is reached, so a violated
  policy fails closed rather than stalling quietly.
* `observe` requires `kind` to equal the `pending` action returned by the last
  `choose`; a caller cannot record an outcome for an action it never selected.
* `from_snapshot` rejects unknown keys, wrong schema/profile, non-int or
  `bool` counters, `blocked > trials`, observation/trial mismatch, a rest
  streak larger than recorded rests, an unknown pending action, and a pending
  rest at the cap.

## Metrics

`src/mojum/motor_play_v2_eval.py` drives the profile through the deterministic
`world_room` fixture and reports, per run: attempts and blocked outcomes per
action, successful moves, rest attempts, idle fraction, longest rest streak,
distinct positions, distinct 0.5 m zones, final position, an action-trace
SHA-256 and a decision-model SHA-256. `physical_contact` is always `false` and
`interface` is `evaluation_only_not_aquarium_abi`.

Fixtures: `canonical`, `open`, `corner`, `blocked_all` (a 0.2 m room where no
cardinal step can succeed) and `held_out_maze`.

```sh
PYTHONPATH=src python -m mojum.motor_play_v2_eval --ticks 240
# or, once installed:
mojum-motor-play-v2 --fixture blocked_all --ticks 240
```

## Non-goals and open work

* Not installed as an Aquarium `--motor-mode`; that requires a versioned
  manifest, replay handling and independent review.
* No claim that the profile navigates intelligently, reduces collisions in
  general rooms, or improves over v1 on every metric. The `blocked_all` fixture
  exists to prove the policy keeps attempting movement under total blockage,
  not to claim success.
* No hardware actuation, MuJoCo contact or real sensory input.
