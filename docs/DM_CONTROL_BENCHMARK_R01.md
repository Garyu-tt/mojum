# dm_control CPU benchmark probe R0.1

This is an **optional benchmark-only** integration slice for Mojum issue #690.
It does not replace \`mojum.world_room.advance\`, the Gymnasium adapter, or the
SB3 held-out benchmark. The physical model has continuous MuJoCo dynamics,
while the canonical point-room is a deterministic kinematic fixture with
swept-AABB collision. Results from the two are not interchangeable.

## Dependency boundary

The Mojum runtime has no new mandatory dependency. The default module and its
stdlib tests do not import \`dm_control\` or MuJoCo. Install the optional pinned
benchmark package only in a separate environment:

\`\`\`sh
python -m pip install "dm-control==1.0.52"
PYTHONPATH=src python -m unittest discover -s tests -p 'test_dm_control_benchmark.py' -v
MOJUM_DM_CONTROL_NATIVE=1 PYTHONPATH=src python -m unittest discover -s tests -p 'test_dm_control_benchmark.py' -v
\`\`\`

The native run is headless and compiles a small MJCF model in memory. It does
not download models, load GLB/URDF assets, open a renderer, access a network
endpoint, or read/write a Noosphere journal.

The pinned \`dm-control\` 1.0.52 PyPI release metadata identifies the package as
Apache-2.0 and Python >=3.9. Mojum itself remains Python >=3.11. See:
- https://pypi.org/project/dm-control/1.0.52/
- https://github.com/google-deepmind/dm_control

The PyPI metadata currently reports no provenance attestation for the 1.0.52
distribution. Pinning a version is not a supply-chain verification; review
the release artifact and transitive dependencies before using it outside CI.

## Workload and output

\`ProbeConfig\` validates an exact, bounded synthetic room description, start
position, timestep, force pattern, obstacle inventory and maximum step count.
\`build_mjcf_xml()\` emits deterministic XML with two slide DOFs, two bounded
motor inputs, a spherical point body and a bounded set of static box obstacles.
There are no external assets or plugins.

\`run_benchmark()\` imports \`dm_control\` lazily, advances a fixed CPU workload,
and reports:
- total elapsed nanoseconds and nearest-rank p50/p95 per-step Python call time;
- contact-bearing steps and maximum simultaneous contact count;
- final planar position and SHA-256 over the ordered measured trajectory;
- package versions and a digest binding the exact workload and generated MJCF;
- peak Python-traced allocations when this call starts tracing itself.

\`peak_traced_bytes\` is Python allocation telemetry, not process RSS or total
native MuJoCo memory. Timing includes the Python wrapper and tracing
instrumentation; compare runs only with the same interpreter, host, package
versions, configuration and instrumentation.

The report decoder is bounded and rejects duplicate JSON keys, unknown fields,
non-finite values, invalid numeric types and digest mismatch. SHA-256 values
are consistency checks, not signatures, authentication or proof of provenance.

## Explicit non-goals

- No Gymnasium registration or SB3 training.
- No parity assertion against \`world_room.advance\`.
- No claim of calibrated contact, clothing/body physics, agent learning,
  physical-device contact or real-world actuation.
- No runtime activation, persistence, private data access or default dependency.
- No automatic comparison/ranking of unlike hardware or simulation workloads.

## Reproduction

The dedicated workflow runs the stdlib contract tests on CPython 3.11, 3.12
and 3.13. Its separate native job installs exactly \`dm-control==1.0.52\` and
runs the small in-memory MuJoCo probe. Keep this feature Draft until both
workflow families pass on the exact PR head and independent QA approves it.
