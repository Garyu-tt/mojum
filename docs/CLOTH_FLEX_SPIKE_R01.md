# Opt-in CPU-first MuJoCo cloth spike R0.1 (#376)

**Status:** experimental, headless, independent real-engine CI added by #588;
first run exposed unsupported mocap-attached elasticity, which this QA follow-up
replaces with an articulated anchor. Acceptance still depends on green reruns. This is **not** an Aquarium component, a garment asset
importer, a GLB skin, a validated cloth-body contact model, or equip/unequip.
It introduces no dependency to the default stdlib-only runtime.

`tools/cloth_flex_spike.py` generates a self-contained MJCF with one `12 x 6`
`flexcomp dim="2"` patch. The x=0 edge is pinned to a **dynamic articulated body** (three slider
joints), coupled by a MuJoCo equality weld to a separate mocap target.
MuJoCo 3.15+ rejects elastic flexes pinned *directly* to mocap bodies because
elastic damping requires attachment velocities. During stepping, the target's
x coordinate follows a deterministic, small sinusoidal motion; the welded
anchor's actual following accuracy must be measured, not assumed. A floor and sphere provide collision geometries.
All simulations remain CPU-only and locally initiated; the script never writes
Aquarium journals, agent state, private traces, or output files.

## Offline structural test (no MuJoCo dependency)

```sh
python3 -m unittest discover -s tests -p 'test_cloth_flex_spike.py' -v
python3 tools/cloth_flex_spike.py --print-mjcf > /tmp/mojum-cloth-spike.xml
```

## Optional physical benchmark

Install MuJoCo separately in an isolated environment. The main package must
not require it. Run with rendering disabled and capture JSON measurements:

```sh
python3 tools/cloth_flex_spike.py --steps 500 --warmup 50 --selfcollide auto --passive true
python3 tools/cloth_flex_spike.py --steps 500 --warmup 50 --selfcollide none --passive false
```

The report includes engine version, generated MJCF SHA-256, vertex count,
measured process wall ms/tick p50/p95, and process peak RSS where available.
It also reports **approximate vertex-level penetrations**, not complete mesh
collision guarantees. Each run starts a fresh model; use identical settings,
hardware and step counts to compare, and repeat samples. RSS is total process
peak, not an isolated solver allocation. The animation trajectory has no
randomness and does not imply physical calibration or real-time capability.

`passive=true` and `passive=false` must be compared separately: passive contact
may save CPU but does not model the same friction behavior as non-passive
contact. `selfcollide=none` ignores important cloth self-contacts; it is only
a performance/quality control, not a final release setting. Passing the
structural tests cannot establish actual solver convergence, preserved
attachment, body contacts or lack of tunneling. Record those results **only
once MuJoCo is run**, with tolerances and CPU target-machine profiles.

## What remains before release

- Independently compile and execute the updated MJCF in the dedicated MuJoCo CI;
  record actual p50/p95, RSS, anchor drift, ground/body penetration and any
  compiler errors. Do not present numbers from missing or skipped engines.
- Add the same rig movement and measured contact scenario for Jolt SoftBody
  and optionally XPBD; do not couple two authoritatively interacting worlds.
- Add an optional garment adapter with bounded LOD and strict rig-version
  compatibility. Clothing loss/fault must leave neural/motor state and
  emergency-stop behavior untouched.
- Verify headless CPU physical tests on 1/2/4 threads and ARM64 as available,
  not merely the XML/template tests in normal CI.

References: [task #376](https://github.com/Garyu-tt/mojum/issues/376),
[MuJoCo flexcomp documentation](https://mujoco.readthedocs.io/en/stable/XMLreference.html#flexcomp),
[MuJoCo upstream drape scene](https://github.com/google-deepmind/mujoco/blob/main/model/flex/drape.xml).
