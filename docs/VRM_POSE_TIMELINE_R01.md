# VRM measured-pose timeline R0.1

tools/vrm_pose_timeline.mjs provides a deterministic offline playback cursor for an already-measured sequence. It is a pure JavaScript ESM helper and has no runtime dependencies.

## Frame contract

Each frame is an exact record with:

- schema: mojum.vrm-pose-timeline/1
- classification: exactly PUBLIC_SYNTHETIC
- sequence: nonnegative safe integer; contiguous across the supplied list
- epoch: bounded body-epoch identifier, identical for the whole sequence
- tick and simulation_time_ns: nonnegative safe integers, strictly increasing
- revision: 64-character lowercase SHA-256 revision string, identical for the whole sequence
- joints: 1..128 unique {id, angle_rad} records; IDs and inventory stay stable, angles are finite and within ±π

A timeline contains 1..10,000 frames. Joint records are emitted in locale-independent Unicode code-point order, so their order does not depend on the host's default locale. Only plain records with enumerable data properties and dense native arrays are admitted; accessors and sparse/extended arrays are rejected without invoking getters. Inputs are copied into detached frozen records. Frames are not sorted or interpolated: input order is authoritative and gaps, duplicate sequence numbers, reordering, time rollback, epoch changes and changing joint inventories fail closed.

## API

~~~js
import {createVrmPoseTimeline} from './vrm_pose_timeline.mjs';
const timeline = createVrmPoseTimeline(measuredFrames);
timeline.current();   // initial frame
timeline.step(1);     // next recorded frame
timeline.seek(12);    // exact zero-based frame index
timeline.step(-1);    // previous recorded frame
timeline.rewind();    // first recorded frame
timeline.position();  // current zero-based index
~~~

seek and step throw on out-of-range navigation; no clamping or fabricated boundary frame occurs. frameAt is an alias of seek.

## Verification

~~~sh
node --test tests/vrm_pose_timeline.test.mjs
~~~

## Boundaries

This is a read-only cursor over caller-supplied PUBLIC_SYNTHETIC measurements. It does not create motion, interpolate missing measurements, load GLB/VRM, call three-vrm, alter a renderer, connect to the body/brain, mutate a simulation, read/write Noosphere, access the network/filesystem, or control devices. A SHA-256-shaped revision is metadata, not authentication. Acceptance still requires independent review and exact-head CI.
