# Physics benchmark comparison contract R0.1

## Purpose

The schema moj um.physics-benchmark/1 (wire identifier: mojum.physics-benchmark/1) is a solver-neutral input contract for comparing separately produced CPU physics measurements. It does not install, select, call, attest, or benchmark a solver. Pinocchio, Jolt, MuJoCo, PBD, and analytic fixtures remain independent opt-in experiments.

Both reports must declare PUBLIC_SYNTHETIC and match the exact workload digest, rig digest, seed, timestep, warm-up count, measured-step count, and generic execution-platform tuple. Solver name and compiled model digest may differ. The comparator refuses envelope mismatch instead of producing a misleading speed ranking.

## Report fields

The JSON object has exactly these fields:

- schema: mojum.physics-benchmark/1
- visibility: PUBLIC_SYNTHETIC
- workload_sha256, rig_sha256, model_sha256: lowercase SHA-256-shaped identifiers
- solver: bounded identifier
- seed: integer in 0..2^31-1
- timestep_ns: integer in 1..1,000,000,000
- warmup_steps: integer in 0..100,000
- measured_steps: integer in 3..10,000
- platform: exactly os, arch, python, cpu_class; each is a bounded generic token
- step_time_ns: exactly measured_steps positive integer samples, each at most 10^12

No extra keys are accepted. bool is not accepted as an integer. JSON ingress is capped at 256 KiB, rejects duplicate object keys and non-finite constants, and requires UTF-8. Do not put hostnames or serial numbers in platform fields.

## Statistics and output

Samples are sorted before calculation. Median is emitted as an exact reduced numerator/denominator pair; p95 uses the nearest-rank definition ceil(0.95 * N). The candidate/baseline median ratio is exact integer arithmetic. A ratio below 1 means the candidate's declared median is lower.

The receipt contains only solver identifiers, aggregate timings, shared workload/rig digests, and SHA-256 checksums of canonicalized input reports. Raw timing arrays, model digests, and arbitrary caller metadata are not copied into the receipt. comparison_sha256 is an integrity checksum, not a signature or authentication.

## Limits

- This is arithmetic over caller-supplied measurements, not independently measured native performance.
- Reports do not prove the solver actually used the declared model, workload, CPU, or timestep.
- A generic platform tuple is a caller assertion, not host attestation.
- PUBLIC_SYNTHETIC is also a caller assertion; it does not authorize PRIVATE or real-user data.
- CPU scheduling, thermal state, compiler flags, native library versions, thread settings, and model topology can still make measurements incomparable. Record those in external experiment provenance and keep runs controlled.
- No absolute performance target, real-time guarantee, contact fidelity, or device/mobile budget follows from the ratio.
- No network, file access, SQLite/Noosphere, Aquarium integration, hardware, or third-party runtime dependency is introduced.

## Reproduction

PYTHONPATH=src python -m unittest discover -s tests -p 'test_physics_benchmark_compare.py' -v

Draft under #702. Exact-head CI and independent review are required before acceptance.
