# Rigid-body IK and cloth benchmark harness R0.1

Issue: #702. This first increment supplies a reproducible **record contract and
reporter**, not a physics-engine integration and not measured performance data.

## Scope and safety

- Pure Python standard library; no required Pinocchio, MuJoCo, Jolt, PBD, NumPy,
  compiler, network, hardware, Aquarium, SQLite, Noosphere or Graph IR access.
- Input must explicitly identify as `PUBLIC_SYNTHETIC` and set `consent: true`.
- No private traces, real hardware signals, tokens, host IDs or user assets.
- The tool validates caller-supplied measurements; it cannot attest that the
  engine produced them or that the rig/asset is equivalent across engines.
- A SHA-256 receipt detects changes to the normalized summary. It is not a
  signature, provenance proof, authentication or engine correctness proof.

## Input and output

The JSON document uses schema `mojum.rigid-cloth-benchmark/1`. Top-level keys
are exact: `schema`, `provenance`, `consent`, `rig`, `engine`, `runs`.
Each run records CPU milliseconds, RSS MiB, optional IK residual, joint-limit
violations and maximum contact penetration in millimetres. Booleans are not
accepted as integers; numeric values must be finite and within fixed bounds.
At most 10,000 observations are accepted.

CLI input must resolve to a regular file; symlinks to regular files remain
supported. On POSIX the file is opened nonblocking and its actual descriptor
is checked before reading, so writerless FIFOs and devices are rejected without
waiting for a writer. POSIX platforms without `O_NONBLOCK` reject input before
opening it. The input budget is 4 MiB, and JSON is limited to 32 nested container
levels before decoding; brackets and escapes inside strings do not count.
Malformed JSON, excessive nesting and integers exceeding the interpreter's
decoder limits produce exit status 2 without a traceback or report publication.
An existing `--output` file is left untouched on input rejection.

Supported engine identifiers are `mujoco-flex`, `pinocchio-ik`,
`jolt-softbody`, and `pbd-xpbd`. Engine versions and platform labels are
required caller-supplied strings. The reporter emits median, nearest-rank p95,
maximum CPU, median/maximum RSS, maximum available IK residual, violation
count, maximum penetration and a deterministic digest.

Example invocation:

```sh
python tools/integration_benchmarks/rigid_cloth_benchmark.py fixture.json
python tools/integration_benchmarks/rigid_cloth_benchmark.py fixture.json --output report.json
PYTHONPATH=src python -m unittest tests.test_rigid_cloth_benchmark -v
```

## Controlled measurement protocol (not yet performed here)

1. Pin exact source commit, compiler/toolchain, engine version, build flags,
   CPU governor, thread count, OS/kernel, and benchmark harness revision.
2. Use the same synthetic asset, rig revision, DOF mapping, SI units,
   timestep, warm-up count, measured step count, and deterministic seed.
3. Separate setup/compile time from steady-state simulation. Run at least
   30 independent repetitions per configuration; report distributions.
4. Measure process CPU time and peak RSS with an external trusted sampler.
   Record wall time only as supplemental data; do not compare unlike hosts.
5. IK: record translation/rotation residual separately in a future schema,
   joint-limit violations and convergence/failure counts. This R0.1 single
   residual field is intentionally coarse.
6. Cloth: record penetration against fixed body/world proxies, stretch/bend
   error, self-collision outcomes and dropped/unstable steps. A zero
   penetration figure must not be interpreted as proof of contact fidelity.
7. Run 1/2/4-thread CPU profiles where supported and a fixed memory budget.
   Reject any result missing exact build identity or equivalent fixture data.

## Upstream and license assessment

- Pinocchio: upstream `stack-of-tasks/pinocchio`; project licensing is BSD
  2-Clause (verify the exact pinned tag and bundled dependency notices before
  redistribution). It is a rigid-body kinematics/dynamics library; IK behavior
  depends on the selected solver and formulation, not a universal built-in
  cloth or contact solver.
- Jolt Physics: upstream `jrouwe/JoltPhysics`; MIT license at the upstream
  repository. Soft-body functionality is relevant to cloth, but documented
  feature limits (including soft-soft collision limitations in the referenced
  5.5 documentation) must be retested against the pinned release.
- MuJoCo: upstream `google-deepmind/mujoco`; Apache-2.0. Its flex elements
  are the in-world baseline candidate, preserving one authoritative contact
  world for body and cloth.
- PositionBasedDynamics: upstream
  `InteractiveComputerGraphics/PositionBasedDynamics`; MIT. Treat it as a
  separate optional solver spike, not a default dependency.

Licenses above are preliminary project-level indicators, not legal advice.
Record exact tags, license files and transitive/native dependency notices before
any implementation or distribution decision.

## Current status and next gate

No engine was built or executed by this harness. No CPU/RAM/IK/contact
performance numbers are claimed. The next useful increment is a controlled,
headless MuJoCo flex spike and a Jolt soft-body spike on the same synthetic
fixture, with captured external measurements. The core Aquarium remains
unchanged and the optional experiment must not block it.
