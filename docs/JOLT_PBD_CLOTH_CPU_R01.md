# Jolt / PBD cloth CPU comparison R0.1 (#702)

**Scope:** an isolated, stdlib-only *synthetic comparison fixture*, with a
working deterministic Python position-based dynamics (PBD) control. This is
not a Jolt Physics engine run, not an independent native integration, and
not a garment system. No changes are made to canonical MuJoCo, Aquarium,
GENESIS, SQLite, Graph IR, policy identity or hardware interfaces.

The opt-in runner is `tools/jolt_pbd_cloth_spike.py`; its tests are in
`tests/test_jolt_pbd_cloth_spike.py`. Neither imports Jolt or adds a
mandatory dependency. It reads no assets, user paths, PRIVATE journals,
model identities, credentials or network services.

## Reproducible offline commands

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_jolt_pbd_cloth_spike.py' -v
python3 tools/jolt_pbd_cloth_spike.py --fixture > /tmp/mojum-jolt-cloth-fixture.json
python3 tools/jolt_pbd_cloth_spike.py --steps 500 --warmup 50 --iterations 6
```

The `--fixture` output is versioned
`mojum.cloth-jolt-fixture/1` JSON with 72 vertices, 126 grid distance
constraints, 110 triangles, six pinned vertices, a floor/sphere, and
the deterministic moving-anchor trajectory
`0.045 * sin(2*pi*step/160)`. Jolt's native `SoftBodySharedSettings`
vertex/face/edge structures are **not** interchangeable with this wire
format. A future native C++ Jolt runner must explicitly translate and
validate these values rather than treating this JSON as an engine input
API. Only public synthetic test data is present.

The PBD baseline is intentionally simple: Verlet integration, distance
projection, one-way floor/sphere point contacts, no self-collision, and
kinematic pinned nodes. It serves a bounded reproducible topology/control
comparison, **not** a physically calibrated equivalent to MuJoCo flex or
Jolt SoftBody. Solver timings use the current process's wall clock, and
RSS is process-wide. Machine-specific timings are not regression constants.

The JSON `final_positions_sha256` is the SHA-256 of coordinates rounded to
nine decimal places after the final step; it is useful for deterministic
replay in a pinned interpreter, but floating-point operation differences
across CPU/OS may affect the last decimal. Tests compare same-process
repeats, not cross-platform bitwise equality. At most 2,000 measured
steps, 500 warmup steps, 32 iterations, and two million projected
edge-operations are permitted. No worker threads are launched.

## Native Jolt comparison boundary (not yet measured)

**Upstream target:** [JoltPhysics](https://github.com/jrouwe/JoltPhysics)
**v5.7.0**, MIT license. Jolt is native C++; soft bodies including cloth
are an upstream capability. The target version is pinned to avoid silent
native ABI/solver changes. Optional community-language bindings may have
distinct API/version compatibility and must be independently audited.

A native runner, if implemented and independently tested, should load
only the above public fixture, enforce resource limits outside the
process, and emit exactly the allowlisted
`mojum.cloth-jolt-native-report/1` shape:
`schema`, `engine` (`JoltPhysics`), `engine_version` (`5.7.0`),
`fixture_sha256`, `steps`, `warmup_steps`, `vertices`,
`dt_seconds`, `cpu_ms_per_step_p50`,
`cpu_ms_per_step_p95`, `max_rss_kib_process` (nullable),
`max_anchor_drift_m`,
`max_floor_vertex_penetration_m_approx`,
`max_sphere_vertex_penetration_m_approx`,
`hardware_contact` (always false).

`validate_native_report(..., expected_steps=..., expected_warmup=...)`
rejects wrong version, fixture, sample count, NaN/Infinity, negative
metrics, unknown fields and any physical hardware-contact claim.
**It is structural validation only, not cryptographic authentication or
proof that Jolt ran.** Source attestation, independent exact-version
native test evidence, external limits, and replay must precede accepting
the report as a measurement. Reports are never automatically persisted.

## Comparability and failure modes

- This fixture uses the same `12 x 6` grid, `0.002 s` timestep and
  floor/sphere topology as the opt-in MuJoCo
  `tools/cloth_flex_spike.py`. It does **not** reproduce MuJoCo's
  elastic constitutive law, pinned servo, contact solver, or
  `selfcollide=auto`. Compare those separately.
- Vertex-center-plus-radius intersection is only an **approximate**
  contact metric; not triangle/edge penetration, cloth/cloth collision
  or proof of body contact. `hardware_contact` is always false.
- Finite, bounded positions are mandatory. Constraint degeneracy,
  nonfinite vertices, budget overflow and malformed reports fail closed.
  No fallback can silently activate a real actuator or another solver.
- CPU/RSS measurements require a dedicated native benchmark with
  machine/OS/threads, compiler, pinned engine, settings, repeat count
  and tail latency recorded. The Python control cannot establish Jolt
  performance. On a power-limited host, run the small default first.

## Before any runtime integration

Build a separately packaged, version-pinned, headless Jolt 5.7.0 native
fixture runner; verify original upstream MIT notice and third-party
notices, ABI and binding support. Reproduce setup and results on 1/2/4
CPU threads and ARM64 as available; collect real p50/p95/RSS and
attachment/contact failures; use independently reviewed exact-head CI.
Keep Jolt confined to optional game/cloth visuals, never the
authoritative virtual movement ledger or hardware. CRDT, Noosphere
replication and device control are out of scope.

Related: [#702](https://github.com/Garyu-tt/mojum/issues/702),
[#376](https://github.com/Garyu-tt/mojum/issues/376),
[`docs/CLOTH_FLEX_SPIKE_R01.md`](CLOTH_FLEX_SPIKE_R01.md).
