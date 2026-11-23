# Point-room swept collision differential audit R0.1

This is an **opt-in, offline, PUBLIC_SYNTHETIC-only** diagnostic for Mojum issue #7.
It does not modify `world_room.py` or decide how an organism moves. The canonical
`world_room.advance` remains the sole simulator for this fixture.

`mojum.world_collision_audit.audit_step(room, state, dx=..., dy=...)` computes
an independently specified intersection of the *exact binary numeric input*
path with each closed obstacle rectangle, using Python `Fraction` and an exact
slab intersection. Every wall or obstacle boundary is included: a tangent
counts as blocked. The oracle then calls the real `world_room.advance` with
its ordinary revision guard and reports agreement/disagreement; it never
replaces actual frame data, fakes a contact, writes a journal, or modifies a
policy. This can reveal a genuine precision-boundary difference between
ideal path arithmetic and rounded floating point arithmetic, not necessarily
a production security failure.

For a bounded deterministic exercise, call `audit_seeded(room, start,
seed=1234, count=32)`. Output includes only counts, revision and SHA-256
checksums; results are reproducible across runs with identical inputs. A
checksum is **not** an authenticated receipt. Use independent trusted inputs
if running an audit over a prior report. Invalid/oversized batches fail before
a step is executed: <=64 obstacle rectangles, <=128 steps, room sides
<=100,000 metres, displacement <=1,000 metres, tick <=1,000,000. The
Python float-to-`Fraction` conversion preserves the exact binary float
rather than guessing the author's decimal intent.

**Boundaries:** no MuJoCo, Jolt, cloth, real-world sensor, wearables, GPU,
network, PRIVATE Noosphere state, production policy control, physics
replacement, SQLite mutation, actor identity or cryptographic attestation.
The diagnostic is deliberately kept separate from already claimed #7 trace
replay, range sensor, world/physics barrier, reachability and occupancy.

Run focused tests with `PYTHONPATH=src python -m unittest discover -s tests -p
'test_world_collision_audit.py' -v`.
