# Offline room-wall clearance R01

Issue #1165, parent #7. CPU-first pure-Python synthetic geometry audit.

Call `audit_wall_clearance((min_x, min_y, max_x, max_y), [(id, x, y), ...])`.
All coordinates use integer millimetres. Room bounds are inclusive and must have
positive width and height. The output contains sorted point IDs, exact integer
distances to left/right/bottom/top walls, nearest-wall distance and a canonical
SHA-256 consistency digest. SHA-256 is **not** an authentication signature.

Inputs must use a four-element native tuple for room bounds, a native list of
points, and a three-element native tuple for each point. Coordinates must be
bounded native integers; booleans, malformed identifiers, duplicate IDs,
out-of-room points, and over-budget requests are rejected. At most 4096 points;
coordinates within ±1,000,000,000 mm. No external data is read. Use only
caller-provided PUBLIC_SYNTHETIC data.

Run focused tests with `PYTHONPATH=src python -m unittest discover -s tests -p test_room_wall_clearance.py`.

This is advisory geometry only. It does not model obstacles, collision shapes,
avatar radius, MuJoCo physics, safety, sensors, actuation, or runtime state.
