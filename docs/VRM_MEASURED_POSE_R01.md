# Measured VRM pose visual projector R0.1 (issue #695)

A small, working JS ESM visual adapter, not a simulator, brain, three-vrm loader, or pre-baked animation player. It uses the documented `vrm.humanoid.getNormalizedBoneNode(bone)` API of [pixiv/three-vrm](https://pixiv.github.io/three-vrm/packages/three-vrm/docs/classes/VRMHumanoid.html), but has **no runtime import or hard dependency**. It accepts a VRM already loaded by a separately authorized browser UI.

```js
import {createVrmPoseProjector} from './vrm_pose_projector.mjs';
const projector = createVrmPoseProjector(vrm, [
  {id: 'hip.yaw', bone: 'hips', axis: 'y'},
]);
projector.apply({
  schema: 'mojum.vrm-measured-pose/1',
  tick: 0,
  simulation_time_ns: 0,
  revision: 'a'.repeat(64),
  joints: [{id:'hip.yaw', angle_rad:0.1}],
});
```

The mapper requires a **full absolute joint snapshot** per tick, finite radian rotations inside [-pi,pi], a lowercase SHA-256 *revision-shaped string* (not a signature), a monotonically increasing logical tick, strictly increasing nonnegative `simulation_time_ns` per accepted tick, and declared, unique normalized humanoid bone axes. Replay reset clears both tick and simulation-time watermarks. All inputs and target bones are checked before mutating visual rotations. `resetReplay()` only resets the renderer, never an organism journal. Any bridge from physics must explicitly map its own unit/coordinate frame and validate the source identity separately.

No solver, auto-animation, browser network, asset loader, character body, real motor command or authority to edit Noosphere state is included. The final Dante VRM, retargeting, draw/render loop and benchmark remain future steps.

Optional Node.js smoke test: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_vrm_pose_projector.py' -v`. In environments without Node, this standalone visualization test is skipped rather than breaking the offline CPU Python baseline.
