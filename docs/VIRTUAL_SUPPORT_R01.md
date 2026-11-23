# Virtual CoM/CoP support witness R0.1 (issue #5)

This opt-in, read-only, stdlib-only diagnostic extends the virtual-body
**observation research**, without altering the canonical point-room or the
existing `mojum.virtual-physics-observation/1` envelope.

## Interface

Call `mojum.virtual_support.observe_virtual_support(packet)`. The required
input schema is `mojum.virtual-physics-support-input/1`, with exact fields:
`schema`, `tick`, `time_ns`, `backend` (`fixture` or `mujoco`),
`synthetic=true`, `physical_contact=false`,
`physical_actuation=false`, `com_m` (engine-reported XYZ in metres),
and `floor_contacts` (0..128 entries). Each contact has exact `id`,
`point_m` (XYZ in metres) and `vertical_force_n` (nonnegative).
All coordinates must be finite within ±10,000 m, and individual forces
finite and at most 1,000,000 N. Logical tick and nanosecond timestamp are
nonnegative signed-64-bit integers.

Output schema `mojum.virtual-physics-support/1` contains the supplied CoM,
force-weighted `cop_xy_m`, total vertical force, number of declared contacts,
number of positively loaded contacts, original virtual-only flags and
`input_sha256` of sorted normalized input. Zero total force means
`cop_xy_m=null`, never a fabricated origin.

Formula: CoP[axis] = sum(contact[axis] * vertical_force) /
sum(vertical_force) for axes x and y, with deterministic ID sorting and
`math.fsum` accumulation. Reversing the input contact order preserves the
derived result and digest. No ground-support polygon, gravitational
calibration, mechanical stability, standing-duration or recovery claim
is made.

## Authority and limits

The caller MUST independently validate engine identity, declared floor
geometry, model hashes, coordinate frame, timestamps, calibration and
provenance. An arbitrary packet with `backend=mujoco` is **not evidence
that MuJoCo ran**. The SHA-256 checksum is for reproducibility, not
cryptographic source authentication. The module does not authenticate
or actuate anything, add journal events, reopen private histories, touch
hardware, control motor gates or authorize e-stop reset. All output remains
synthetic. It is not a substitute for live physics testing or the five-second
standing and perturbation acceptance milestones in #5.

## Test

From repository root:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_virtual_support.py' -v

No optional engine or network is needed; Python 3.11–3.13 intended.
