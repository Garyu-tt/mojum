# MuJoCo CPU articulated-body bridge R0.1 (issue #689)

Status: **opt-in, experimental, synthetic two-link fixture only**. It does **not** make the
released 2D Aquarium a 3D organism. No normal startup path imports MuJoCo. No
Noosphere events, genome mutations, user audio, secrets, devices, or network are
accessed. No actuators or motor targets are generated: `model.nu == 0` is
checked at runtime. Sensor outputs are strictly virtual solver measurements,
never claims about contact with actual hardware or a human body.

## Boundaries

- `mojum.body.rig/1`: an explicitly supplied, bounded synthetic chain with
  `rig_id`, exact 2 ms timestep, root height, 1..32 single-DOF hinge joints,
  ASCII joint names, unit axes, finite ordered radian limits including zero,
  capsule dimensions and positive masses. Unknown fields, bool numerics,
  duplicate joints, invalid axes and overbudget geometry fail closed.
- `compile_mjcf(rig)`: deterministic no-assets MJCF with fixed dt, gravity,
  ground and nested hinge/capsule bodies. Explicit `<compiler angle="radian"/>`
  preserves the radian `limits_rad` wire contract in the compiled MuJoCo model.
  No `actuator`, `freejoint`, `mocap`
  or unsafe file references. The SHA-256 `rig_revision` is an integrity digest,
  not a signature or permission token.
- `HeadlessBody(rig)`: the **only** MuJoCo loader, invoked explicitly. Exact
  `mujoco==3.17.0` is required. It validates expected topology and never
  forwards a motor command. `step(n)` is bounded to 1..10000, validates every
  intermediate observation, and latches failures on nonfinite qpos, qvel,
  time, contact distances or oversized contact counts.
- `mojum.physics-observation/1`: ordered measured joint state with limits and
  solver-limit deviations, stable virtual tick/time and the bounded contact
  manifold with named geometries and signed separation. `hardware_contact`
  is always `false`. These typed observations are **not yet wired** into
  GENESIS or a neural policy. Neither `mechanicus.body/1` nor
  `mechanicus.physics/1` is advertised as implemented.

## Reproduction

Offline stdlib fixture, no MuJoCo or external assets:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mujoco_body.py' -v
PYTHONPATH=src python -m mojum.mujoco_body --print-mjcf
```

To test the **real** virtual physics, in a separately approved isolated CPU
Python 3.11+ environment, install **exactly** `mujoco==3.17.0` and check the
native test cases are NOT skipped. No installation is performed by Mojum.

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mujoco_body.py' -v
PYTHONPATH=src python -m mojum.mujoco_body --steps 50
PYTHONPATH=src python -m mojum.mujoco_body --benchmark --steps 500 --warmup 50
```

Native acceptance must include engine load success, 2-link topology, no
actuators, repeated fresh-run trajectories/contact signed distances within
`1e-9` on the same CPU+engine, and no nonfinite states. Collect repeated
p50/p95 step wall times and total-process RSS on the target machines before
setting any performance claim; benchmark wall time is not CPU utilization.
In the local stdlib-only test run, 6 contract tests pass and 2 native tests
skip when MuJoCo is absent; **this is not a MuJoCo runtime pass**.

## Provenance and licensing

- MuJoCo source: <https://github.com/google-deepmind/mujoco>; pinned **3.17.0**
  runtime for this prototype. Verify exact upstream release, Apache-2.0
  licensing and transitive-wheel licensing independently before distribution.
- MuJoCo Menagerie: <https://github.com/google-deepmind/mujoco_menagerie>.
  **No Menagerie models are fetched, embedded or used in this PR.** Their
  per-model/source/texture licenses and body-to-GENESIS DOF mapping require
  a separate asset-level review; Menagerie itself is not blanket clearance.
- Pinocchio FK/IK comparison remains separate issue #702. This adapter has no
  Jolt/cloth engine and does not reinterpret `tools/cloth_flex_spike.py`.

## Release blockers

1. Native exact-version MuJoCo tests and perf/RSS numbers have not been run
   in the stdlib-only local environment. CI with an opt-in engine is required.
2. Need independently reviewed real humanoid model/asset license and
   validated GENESIS DOF/rig compile contract; the toy capsule chain is not an
   anatomical or calibrated motor model.
3. Neural sensory mapping, authorization and safety-reviewed motor-intent gate
   remain future work. No commands to physical robots, network, devices or
   persisted PRIVATE histories should pass through this prototype.
