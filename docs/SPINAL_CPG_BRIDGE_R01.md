# Synthetic CPG → MotorGate bridge R01

Status: opt-in, synthetic, CPU-only adapter contract. This is not a locomotion
controller, physics solver, calibrated rig, or hardware interface.

## Boundary

`BilateralCPG` emits a bounded dimensionless bilateral waveform. The bridge
maps that waveform through explicit `JointChannel` calibration records into
`MotorIntent` values, then validates every intent with the existing
`MotorGate` before returning the complete tuple. A stale profile revision,
malformed frame, duplicate joint, invalid calibration, e-stop, or out-of-limit
target fails closed.

The bridge does not call MuJoCo, apply forces, write to devices, or bypass the
MotorGate. It has no mandatory third-party dependencies. The MuJoCo body
adapter remains a separately reviewed optional integration; this module does
not import it.

## Units and calibration

- CPG outputs are integer milliscale values in [-1000, 1000].
- `center_millirad` and `scale_millirad` are caller-supplied virtual joint
  calibration in milliradians.
- The bridge computes the target using integer arithmetic, then converts the
  result to radians for the existing MotorGate API.
- MotorGate's configured limits are authoritative. A channel calibration
  outside those limits is rejected rather than clipped.

A channel selects exactly one side (`left` or `right`). The mapping is
explicit; no anatomical or rig mapping is inferred.

## Feedback

`feedback_from_loads` constructs a CPG `SensoryFeedback` record from
caller-supplied integer load proxies. These values must be explicitly synthetic
and bounded by the CPG contract. The bridge does not infer load from geometry
or privileged future world state.

## Run focused tests

```sh
python -m unittest tests.test_spinal_cpg_bridge -v
```

This is a focused adapter contract test only. It does not establish physical
standing, perturbation recovery, or walking. Exact-head CI and independent
review are required before merge.
