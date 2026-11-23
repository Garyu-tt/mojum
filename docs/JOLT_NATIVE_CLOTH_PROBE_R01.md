# Jolt native cloth probe R0.1

## Purpose

This isolated acceptance slice closes the missing native-engine smoke in the #702 Jolt/PBD study. It builds and runs an actual Jolt Physics **5.7.0** C++ soft-body cloth fixture. It is separate from the Python PBD control and native-report shape validator in PR #728.

The probe creates a 12 × 8 triangular cloth mesh, pins its first row, generates distance/bend/LRA constraints using Jolt's `SoftBodySharedSettings`, and advances 240 fixed 1/60 s CPU steps under gravity. A strict JSON result reports particle/face counts, bottom-row drop, pinned-row drift and finite-state checks.

## Reproduce

Requirements: CMake 3.20+, a C++17 compiler, Git, and Python 3.11+ for the report validator. The build fetches exactly the upstream `v5.7.0` tag at configure time. No Jolt package is installed into Mojum and there is no Python runtime dependency.

```sh
cmake -S benchmarks/jolt_native_cloth -B build/jolt-native-cloth -DCMAKE_BUILD_TYPE=Release
cmake --build build/jolt-native-cloth --parallel 2
JOLT_NATIVE_CLOTH_PROBE="$PWD/build/jolt-native-cloth/jolt_native_cloth_probe" \
  python -m unittest discover -s tests -p 'test_jolt_native_cloth_probe.py' -v
```

The CI workflow performs the same native build and executes the binary as part of the report validator.

## Safety and interpretation

- Synthetic cloth only; no user assets, avatar, PRIVATE Noosphere records, network services, or actuators.
- CPU headless probe; no renderer, GPU dependency, or mandatory application integration.
- Native worker pool is capped at eight threads, independent of how many logical CPUs the host exposes.
- Output deliberately omits source paths, host identifiers and raw physics internals.
- Native JSON report is limited to 16 KiB before parsing; duplicate object keys and non-finite JSON constants are rejected.
- `self_collision=false` and `external_contact=false` are hard-coded capability limits, not implied features.
- This verifies only that this tiny pinned soft body compiles, advances and deforms under gravity while its pinned row remains stable. It does not establish garment fit, body collision, self-collision, material calibration, stable long-run behavior, mobile performance, or production integration.
- Jolt upstream release is MIT licensed; the upstream project documentation notes soft bodies are still in development. Review current upstream license and transitive build dependencies before redistribution.

## Upstream references

- Jolt Physics v5.7.0: https://github.com/jrouwe/JoltPhysics/tree/v5.7.0
- SoftBodySharedSettings v5.7.0: https://jrouwe.github.io/JoltPhysicsDocs/5.7.0/class_soft_body_shared_settings.html
- Soft-body creation: https://jrouwe.github.io/JoltPhysicsDocs/5.7.0/class_body_interface.html
- Jolt architecture / soft bodies: https://jrouwe.github.io/JoltPhysicsDocs/5.7.0/

## Remaining gates

Draft only. Require exact-HEAD native workflow success, existing Mojum required CI, source review, independent account-distinct QA and license review. No automatic merge or main-branch changes.
