# Mojum motor slew guard R0.1 (virtual-only)

**Scope:** optional, isolated, CPU-only admission gate for *synthetic joint-position targets* under parent issue #5. It neither commands hardware nor establishes MuJoCo/physical safety, force/torque limits, dynamic calibration, multi-process exclusion, persistence or crash recovery.

## Contract

`MotorSlewGuard(limits, initial, tick=0, revision=0)` receives plain `dict`s of 1–128 safe ASCII joint names. Each limit is `(min_target, max_target, max_change_per_virtual_tick)` of finite numeric values. All initial targets must be within limits. Every accepted frame supplies **exactly** the same joint names, `tick=previous_tick+1`, and `expected_revision=previous_revision`. The complete frame is validated before state mutation. A failed frame changes neither tick, revision nor targets; no partial joint admits.

For every joint, `abs(new_target - previous_target) <= max_change_per_virtual_tick` must hold. An idle joint must be repeated at its previous target; skipped virtual ticks do not expand the limit. Only concrete `int`/`float` numbers (not booleans, NaN or infinity) are admitted. The 63-bit nonnegative time/revision counters cannot overflow. Inputs are detached from the accepted state; the `VirtualFrameReceipt` is frozen and contains sorted immutable `(joint, target)` pairs. No file, database, network or wall-clock reads occur.

`trip_estop()` latches a rejection for every subsequent frame. There is intentionally **no reset method**: explicitly constructing a fresh guard is an external orchestration decision and not a safety proof. Revisions are **local optimistic sequence counters**, not a Noosphere or hardware authority token.

## Minimal demo

```python
from mojum.motor_slew_guard import MotorSlewGuard, MotorSlewError

gate = MotorSlewGuard({"elbow": (-1.0, 1.0, 0.1)}, {"elbow": 0.0})
first = gate.advance({"elbow": 0.1}, tick=1, expected_revision=0)
assert first.revision == 1
try:
    gate.advance({"elbow": 0.3}, tick=2, expected_revision=1)
except MotorSlewError:
    pass  # Too fast: no change is committed.
assert gate.receipt() == first
```

## Verification

From repository root: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_motor_slew_guard.py' -v`. Tests cover adjacent-tick enforcement, per-joint limits, all-or-nothing rejects, stale revisions, e-stop, counter overflow, mutated input dictionaries, hostile collection types, malformed names and numeric values. This additive fixture is for independent QA in a draft PR; it does **not** make Mojum suitable for physical actuation.
