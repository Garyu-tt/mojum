# RoboPianist piano-key benchmark: opt-in, offline R0.1 (#700b)

This is a **read-only measurement adapter**, not a trained piano-playing agent,
MuJoCo simulation, game-school grade, keyboard actuator, or physical contact
sensor. It does not install, import, download or run RoboPianist. The
`mojum` Python 3.11+ baseline remains stdlib-only.

## Verified upstream boundary

- Upstream: https://github.com/google-research/robopianist
- RoboPianist's shadow-hands task publishes a flattened `goal` array of
  `(lookahead+1) * (88 keys + 1 sustain)` entries. The current target is
  **only** the first 89 elements. Its piano model measures
  `piano.activation` and `piano.sustain_activation`, which must be taken
  after an actual virtual physics step, never copied from the command/action.
- The separate physics engine is MuJoCo. Upstream code is Apache-2.0,
  **not** a blanket clearance for its Menagerie hand assets, MIDI files,
  soundfonts or derived datasets. Those require their own source/LICENSE
  reviews and explicit user approval before use with private school data.
- **Pinning policy:** records of any exported real virtual run must contain
  the exact lowercase 40-character upstream Git commit in `upstream_ref`.
  This PR does not select an unverified upstream commit or lock an uninstalled
  package. No network or external model is required for the included demo.

References:
- https://github.com/google-research/robopianist/blob/main/robopianist/suite/tasks/piano_with_shadow_hands.py
- https://github.com/google-research/robopianist/blob/main/README.md

## Running the actually executable slice

From the repository root:

```sh
PYTHONPATH=src python -m mojum.robopianist_keyscore --demo
PYTHONPATH=src python -m unittest tests.test_robopianist_keyscore -v
PYTHONPATH=src python -m mojum.robopianist_keyscore --input /path/to/public-synthetic-trace.json
```

`--demo` scores an in-memory four-frame, seeded **synthetic** public fixture,
including a frozen **silent/no-learning** baseline. The output never asserts
successful motor learning or actual physics execution.

An owner-run offline export from a separately installed and licensed
RoboPianist instance can explicitly populate a frame by calling:

```python
from mojum.robopianist_keyscore import from_robopianist_arrays

# AFTER a real MuJoCo physics step; use a pinned upstream checkout.
# goal is the task's public 'goal' observation, flattened.
# activation/sustain are measured virtual piano state, not model actions.
frame = from_robopianist_arrays(
    tick=0, timestamp_ns=0,
    goal=goal_array.tolist(),
    activation=measured_piano_activation.tolist(),
    sustain_activation=float(measured_sustain_activation),
)
```

Append such frames to an **explicitly public, detached** record, e.g.:

```json
{
  "schema": "mojum.robopianist.keys/1",
  "source": "robopianist-explicit-export",
  "upstream_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "seed": 1,
  "frames": []
}
```

The SHA here is a **format-only placeholder**, NOT a verified upstream pin;
replace it with the actual committed checkout hash. Non-empty frames are
required. `frame` includes `tick`, `timestamp_ns`, sorted `target_keys`
and `pressed_keys` (0..87), boolean sustain values and optional measured
`finger_contacts` (left/right, finger 0..4, key 0..87). Unknown JSON fields,
duplicate JSON keys, repeated/reordered keys, duplicate physical fingers,
NaN/Inf, gaps in tick, nonmonotonic timestamps and out-of-range values fail.

The adapter's activation threshold is `>=0.5`, intentionally an **evaluation
choice** rather than a claim of identical RoboPianist shaped rewards. Reported:
key micro-F1, exact frame+sustain rate, frozen silent-control rate and delta,
sustain accuracy, and how many matching pressed keys have explicitly reported
virtual finger observations. No privileged future goal enters the score;
lookahead values are validated then ignored.

## Safety, resource budget, and limitations

- Opt-in only. No direct Aquarium/GENESIS/Noosphere connection, task scheduler,
  reward loop, grade, private history access, hardware I/O, network, or writes.
- Parse at most 1 MiB JSON; score 1..4096 frames; exactly 88 piano keys, at
  most 33 target lookahead rows, 10 finger-contact samples per frame, and
  strict scalar/timestamp bounds. Runtime O(frames * (88 + contacts)) and
  small bounded Python memory; no GPU allocation.
- Synthetic tests cover exact replay, no-learning control, malformed data,
  wrong provenance and large input. **No installed RoboPianist/MuJoCo,
  numerical physics comparison, contact fidelity, model training or measured
  host RAM/latency is claimed in this slice.**
- Next independent step: pinned licensed upstream env in a disposable offline
  CPU process, sampled explicit `goal` and `activation` arrays, then held-out
  seeds and measured CPU/RAM/physics reproducibility against no-learning.
  Independent exact-head CI/QA must approve before any merge. Integration
  catalog issue #700 stays `planned` until upstream license review.

