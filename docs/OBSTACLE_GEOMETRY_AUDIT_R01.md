# Offline obstacle geometry audit R0.1

Issue #1162, parent #7. Import `audit_obstacles` from `mojum.obstacle_geometry_audit`.

Input is a native list (maximum 128) of exact native dicts containing `id`, `x_min`, `y_min`, `x_max`, `y_max`. Field names must be native strings of at most 32 characters. Coordinates are native integers in millimetres within ±1,000,000. IDs are unique ASCII alphanumeric, underscore or hyphen, 1–64 characters. Bounds are strictly increasing. The caller is responsible for supplying only PUBLIC_SYNTHETIC data; this helper does not inspect or authenticate provenance or visibility.

Output contains sorted per-obstacle area/perimeter, sorted positive-area pairwise overlaps, a schema identifier and a SHA-256 digest of canonical JSON excluding the digest. Touching edges do not overlap. The digest provides consistency only, not authentication. Inputs are never mutated.

No room runtime, physics, collision resolution, occupancy grid, filesystem, network, device access or private data. This is an offline diagnostic, not an actuator safety boundary.

Run: `PYTHONPATH=src python -m unittest tests/test_obstacle_geometry_audit.py` from repository root (or unittest discover). Independent QA and exact-head CI are required before merge.
