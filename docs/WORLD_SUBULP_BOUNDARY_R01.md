# World R0.1: sub-ULP outward room-boundary admission

Scope: issue #7, canonical CPU-first *synthetic point-room* `world_room.advance`.
This is not MuJoCo, a motor-safety certification or a collision oracle for a
physical avatar.

## Defect and correction

IEEE-754 addition can round a strictly outward step to an unchanged boundary
coordinate. Example: `width = x = 1.0; dx = 1e-16` gives `x + dx == 1.0`.
Previously `advance` checked only the rounded target, observed the position
as in bounds, and returned `blocked=False` despite the commanded step
mathematically pointing outside the room. The read-only rational differential
audit in PR #827 independently identified the issue.

The new narrow guard checks exact `Fraction` arithmetic **only** when the
rounded endpoint is at an outside boundary and the delta points outward:
right, top, left and bottom room edges. `Fraction(int|float)` is exact for
already validated Python finite input scalars. A strict crossing is blocked;
an exact landing, zero step or sub-ULP inward step remains permitted.
`world_room.advance` still increments the logical tick and computes a new
revision on a blocked step. No global epsilon/tolerance or implicit clock is
introduced. Existing continuous swept-AABB obstacle checks are unchanged.

## Replay compatibility and acceptance guard

This is a **behavior correction to the existing v1 synthetic fixture**, not
a new journal/event schema. Historic observations containing the pathological
sub-ULP outward commands may differ in `blocked` on deterministic replay.
Real canonical journal compatibility has **not** yet been independently
established on the exact submitted commit: do not silently rewrite old
PRIVATE journals or claim cross-version replay equivalence. If such
historical commands exist, pin the older engine for replay or design an
explicit versioned migration before accepting this fix to `main`.

Ordinary zero/valid/step-sized synthetic movements preserve existing behavior.
The fix is CPU-stdlib-only; it imports `fractions.Fraction` lazily on the
narrow near-wall condition (stdlib import at module load, conversion only at
wall). No network calls, hardware controls, Noosphere writes or user data.

## Deterministic tests

`tests/test_world_room_subulp_boundary.py` covers both room dimensions,
upper-edge sub-ULP commands, an interior start whose exact sum crosses
the edge but rounds back to it, valid exact and rounded interior landings,
legitimate inward/zero steps, pre-existing lower-bound rejection, diagonal
step atomicity, large integer widths, deterministic replay and revision CAS.

Run using the checked-out branch with `PYTHONPATH=src python -m unittest
discover -s tests -p 'test_world_room_subulp_boundary.py' -v`, then full
stdlib matrix (3.11, 3.12 and 3.13), including Python 3.13 SQLite leak gate.
Independent exact-head QA and protected-main acceptance are required.
