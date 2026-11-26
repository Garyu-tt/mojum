# Cloth seams R0.1 (issue #376)

Optional, stdlib-only, offline **PUBLIC_SYNTHETIC** seam-pair admission
and connected-group report. No dependency, hardware access, Noosphere reads,
cloth solver, or live organism mutation.

API: `mojum.cloth_seams.audit_seams(vertex_count, pairs)`.

Input is a list/tuple of two-index list/tuple pairs. Vertex indices are exact
nonnegative integers below vertex_count. Self-links and duplicate undirected
pairs are rejected, including reversed duplicates. Maximum 100,000 vertices
and 200,000 pairs. A seam cycle is valid; no claim of manifold geometry,
physical stitch stability, seam orientation or edge incidence is made.

Result groups contain only vertices appearing in seams; groups and members
are deterministically sorted. Failure raises ValueError, without partial
commit or changing input. This is a data admission helper, not a safety
certification for simulated or physical garments.

Run: `PYTHONPATH=src python -m unittest tests.test_cloth_seams -v`.

Future integration requires explicit caller-side schema/visibility checks,
an independent exact-head CI run and review before merge.
