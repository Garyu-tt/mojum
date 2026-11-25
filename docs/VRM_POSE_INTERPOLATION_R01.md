# VRM measured-pose interpolation R0.1

This optional stdlib utility interpolates between two **already measured** pose frames for read-only visualization. It is not an animation system, motion predictor, physics solver, or motor controller.

## Contract

- Input schema: `mojum.vrm-measured-pose/1`; output schema: `mojum.vrm-pose-interpolation/1`.
- Both inputs must be exact native dictionaries marked `PUBLIC_SYNTHETIC`, with the same body epoch, rig revision, coordinate system and exact joint inventory.
- Source timestamps must be strictly increasing. The requested timestamp must lie within the closed source interval; extrapolation is rejected.
- Joint coordinates are finite bounded metre-space XYZ triples. Every per-joint component delta must remain within the caller-supplied discontinuity bound.
- Joint names are sorted for deterministic output. Output is detached and immutable; its SHA-256 digest is a consistency witness only, not authentication.

## Example

```python
from mojum.vrm_pose_interpolation import interpolate_measured_poses

pose = interpolate_measured_poses(
    frame_a,
    frame_b,
    target_timestamp_ns=1_050_000_000,
    max_joint_delta=0.25,
)
# pose.coordinates is aligned with pose.joint_names
```

## Boundaries

No extrapolation, generated motion, missing-joint fill, cross-epoch retargeting, prediction, GLB loading, three-vrm integration, file/network/database access, private memory access, or actuator authority. The caller must first validate provenance and frame admission; this module's `PUBLIC_SYNTHETIC` field is caller-asserted. It does not prove real sensor calibration or physical motion. No mandatory dependency is added.
