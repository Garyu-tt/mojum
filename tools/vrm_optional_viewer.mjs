/**
 * Opt-in three-vrm bridge. Requires app-local pinned `three` and
 * `@pixiv/three-vrm` packages. Never called by the default Mojum runtime.
 */
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin } from '@pixiv/three-vrm';
import { createPoseProjector } from './vrm_pose_bridge.mjs';

import {inspectOfflineGlb} from './vrm_glb_guard.mjs';

/** Caller must explicitly supply a local File/ArrayBuffer; never a URL. */
export async function loadLocalVrm(buffer, mapping) {
  inspectOfflineGlb(buffer);
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await new Promise((resolve, reject) => {
    loader.parse(buffer, '', resolve, reject);
  });
  const vrm = gltf.userData?.vrm;
  if (!vrm) throw new TypeError('local GLB has no VRM extension');
  return {vrm, projector: createPoseProjector(vrm, mapping)};
}
