# Synthetic world-object interactions R0.1 (issue #7)

An **opt-in**, deterministic CPU-first object interaction sidecar for the canonical Mojum point-room fixture. This is **not** 3D physics, MuJoCo, a learned policy, physical grasp/touch sensing or an actual device controller. Importing it does not activate IO.

## Contract

- initial_objects(room, initial_room_state, fixtures) creates up to 64 synthetic point objects, each [id, x_m, y_m]. Fixture birth is allowed only at room tick 0. IDs are unique, bounded ASCII; positions must be free and not touch an obstacle. Input lists are detached and sorted by ID.
- advance_objects(room, old_room_state, old_objects, *, tick, dx, dy, expected_room_revision, expected_objects_revision, action, object_id, place) makes exactly one joint room/object transition. Both states require valid revisions and identical prior logical ticks. The canonical world_room.advance executes actual swept-AABB actor motion, so a caller cannot inject an arbitrary next position.
- Each tick may have one wait, pick or place action. Pick requires a named uncarried object within 0.35 m of the **new actor position**, with unobstructed closed-AABB line-of-sight. One carried slot is encoded as (id, None, None). Place requires the exact carried ID and finite position within 0.35 m in free geometry and along an unobstructed segment.
- Invalid actions raise ValueError with no partial mutations. Blocked actor movement is still a valid single logical tick, reported as move_blocked=true. Every event has synthetic provenance and physical_contact=false.
- Object revisions SHA-256 hash the schema, logical tick, canonical room revision, sorted item positions and carried ID. This is a consistency digest, **not** a signature, authority, encryption or proof of an actual object interaction. The trusted caller must enforce ownership of authoritative history.
- snapshot_objects(room, state) emits detached JSON-safe fields. restore_objects(room, room_state, snapshot) validates exact schema and keys, tick, room revision, sorted item entries, carried invariants and digest before admitting a snapshot. No SQLite, filesystem, network or Noosphere writes.
- Numeric geometry requires finite built-in int/float (not bool); ticks are built-in integers bounded at 2^53-1, object count is at most 64.

## Minimal synthetic example

~~~python
from mojum.world_room import Room, initial_state
from mojum.world_objects import initial_objects, advance_objects, snapshot_objects

room = Room(2, 2)
actor = initial_state(room, 0.5, 0.5)
items = initial_objects(room, actor, (("cube", 0.6, 0.5),))
actor, items, pick = advance_objects(
    room, actor, items, tick=1, dx=0, dy=0,
    expected_room_revision=actor.revision,
    expected_objects_revision=items.revision,
    action="pick", object_id="cube")
actor, items, drop = advance_objects(
    room, actor, items, tick=2, dx=0, dy=0,
    expected_room_revision=actor.revision,
    expected_objects_revision=items.revision,
    action="place", object_id="cube", place=(0.7, 0.5))
assert items.carried is None
assert drop["physical_contact"] is False
print(snapshot_objects(room, items))
~~~

## Reproduce

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_objects.py' -v
~~~

The focused hermetic suite checks sorting, nested detachment, valid pick/drop, canonical swept motion, thin-wall occlusion, false teleports, stale/corrupted CAS revisions, invalid geometry and nonfinite/bool input, snapshot tampering and deterministic replays. Python 3.11+, stdlib only.

## Explicit limitations

This is a detached synthetic point-object state machine, **not** a simulation of mass, object radius, forces, grasp shape, 3D occlusion, deformable cloth, multi-agent simultaneity or body physics. A carried token is a symbolic inventory slot, not a rigid body parented to a limb. No inference about real contact may be made from it. It does not wire itself into the canonical Aquarium runner, PRIVATE journal, external worlds or hardware. Independent exact-HEAD GitHub CI and QA are required before merging.

Ownership: only src/mojum/world_objects.py, tests/test_world_objects.py and this document; world trace, range sensor, tick barrier, occupancy, reachability, external frame and collision-oracle slices are untouched.
