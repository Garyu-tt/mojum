# Experimental read-only three-vrm projection (#695)

**Status:** optional viewer adapter/spike, NOT a connected body in Aquarium and NOT a validated walking/physics renderer. Stable `main` remains Python 3.11+ stdlib-only. Default installations do not import `three`, fetch assets or start browsers. No prepared animations, pose interpolation or motor proposals are produced here.

## Contract

`tools/vrm_pose_bridge.mjs` (zero imports) accepts a map `mojum.vrm-bone-map/1` of **exact** synthetic source joint identifiers to normalized VRM humanoid bone names and observed `mojum.vrm-pose/1` frames. Frames carry `tick`, `source_revision`, a root position (metres) and unit quaternions in `vrm-normalized-local` coordinates. This coordinate contract is *not* an automatic conversion from an arbitrary MuJoCo/GENESIS rig. An upstream physical-body producer must explicitly implement and calibrate that conversion in a separate tested PR. Missing or extra source joints, missing VRM mapped bones, duplicate target bones, invalid quaternion norms, non-finite values, changed same-tick revisions, gaps, and implicit rewind are rejected before mutation. Explicit `reset()` is required before manual replay/rewind. The adapter never writes Noosphere or alters simulation.

The sidecar `tools/vrm_optional_viewer.mjs` demonstrates the actual three-vrm integration boundary: `GLTFLoader`, `VRMLoaderPlugin`, `gltf.userData.vrm`, `getNormalizedBoneNode`, and read-only quaternion/root updates. `loadLocalVrm(arrayBuffer, mapping)` accepts a local binary GLB/VRM only, max **32 MiB**, checks the GLB v2 header and JSON chunk, and rejects external `uri` dependencies; it never accepts URLs or triggers fetch in project code. This is a **resource-filtering guard, not a secure GLB parser or malware sandbox**. Use a sandboxed viewer for untrusted files. VRM rendering must never drive the policy or simulated contacts; never attach an animation mixer to it.

## Opt-in developer check

```bash
node --test tests/vrm_pose_bridge.test.mjs
```

To wire into an independently reviewed browser renderer, install separately pinned and license-reviewed `three` and `@pixiv/three-vrm` packages and bundle the sidecar with your existing frontend toolchain; explicitly call `loadLocalVrm` with a locally selected binary and the fixture map, add returned `vrm.scene` to your scene, then call `projector.apply(frame)` on each verified committed measured tick. The synthetic-only fixture is `examples/vrm_pose_fixture_r01.json`. **No network CDN, automatic install, mandatory dependency, browser deployment or mobile smoke has been added.**

## Acceptance limits / upstream

- Upstream: https://github.com/pixiv/three-vrm (license and exact version/asset redistribution clearance **pending**, no packages vendored). Check `three`'s exact license/transitives too. `src/mojum/data/optional_integrations.json` remains `planned`/`required`.
- CPU: 3 mapped joints in fixture, max 55 joints accepted and at most one advancing frame/tick; JSON validation O(joints), copies bounded arrays, no runtime I/O. Rendering CPU/GPU/frame-time/RAM on Pixel 9, Vive, and desktop **not measured**; browser smoke required before acceptance.
- Not verified: REAL engine loading without installed dependencies; real avatar skeleton compatibility, calibrated 3D pose, runtime animation absence elsewhere, body self-collision or tactile output, mobile browsers, sandbox security. No claim of full #695 completion.
