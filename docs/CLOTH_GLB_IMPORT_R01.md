# Isolated GLB2 cloth geometry importer (issue #376, partial R0.1)

This **offline opt-in geometry experiment** extracts bounded triangle positions, optional four-weight skin influences and user-selected pinned vertices from one small PUBLIC_SYNTHETIC `.glb`. It does not change `Aquarium`, `GENESIS`, `Noosphere`, the wardrobe state machine in #740, or the MuJoCo flex / Jolt/PBD solver. It never downloads dependencies or accesses external URLs from a GLB file.

## Contract and security

- Input: one GLB2 JSON+BIN file with exact caller-supplied SHA-256, glTF 2.0 asset, exactly one mesh and one TRIANGLES primitive. Little-endian VEC3 float POSITION, optional VEC4 JOINTS_0 and WEIGHTS_0, optional uint8/16/32 index accessor. Non-sparse interleaved attributes are supported. A skinned primitive must have a single matching mesh-to-skin node; only *identity* mesh node transforms are accepted. Mesh coordinates remain local right-handed, +Y up, metres; no world-space or inverse-bind-matrix application is implied.
- Caller must supply an asset ID, SPDX license (`CC0-1.0`, `CC-BY-4.0`, `MIT`, `Apache-2.0`), rig revision, body profile and *exactly one* anchor strategy (`--pin-index` for an explicit vertex or `--anchor-joint` with weight threshold). This is a **PUBLIC_SYNTHETIC-only contract**, not permission to process private Dante meshes. The labels are caller assertions, **not a DRM or license validation service**.
- Output schema: `mojum.cloth.glb-proxy/1`, including source digest, provenance assertion, full positions/triangles, AABB, rig/body labels, anchor vertex IDs, optional joint names + skin influences, explicit `physical_cloth=false`, `simplified_lod=false`, `body_collision_proxy=false`, `hot_equip=false`. Geometry is **not simplified**: the input itself must already be a low-resolution physics proxy; do not mislabel it as an offline decimator.
- Strict bounds: input ≤8 MiB, JSON ≤1 MiB, ≤4096 vertices, ≤8192 triangles, ≤128 skin joints and ≤1024 pins. Reject invalid/duplicated JSON keys, nonfinite positions/weights, degenerate triangles, out-of-bounds accessors, malformed chunk lengths, external buffers, nonzero BIN padding, unsupported extensions/sparse/morph/animation, non-identity mesh transforms, ambiguous mesh/skin references, duplicate anchors, bad digest/rig labels. No GPU, native parser, image codec, child process or dynamic code.
- CLI reads only a regular local file (POSIX `O_NOFOLLOW` for the final input path), stages output mode 0600, then atomically hard-links without replacing any existing file. No origin bytes or local path are printed on error. Source paths with symlinked parent directories and untrusted file changes between a caller's SHA calculation and invocation still depend on the digest check; use a private directory and inspect rights in production. A crash may leave an unreferenced temporary file; the caller owns cleanup.

## Reproducible synthetic run

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_glb_import.py' -v
sha256sum /path/to/public_synthetic_scarf.glb
PYTHONPATH=src python -m mojum.cloth_glb_import \
  --input /path/to/public_synthetic_scarf.glb \
  --output ./scarf.proxy.private.json \
  --expected-sha256 <64-lowercase-hex-digest> \
  --asset-id synthetic-scarf-r1 --license CC0-1.0 \
  --rig-version rig-r1 --body-profile public-synthetic \
  --pin-index 0 --pin-index 1 --allow-public-synthetic-import
```

For a skinned mesh replace `--pin-index ...` with `--anchor-joint neck --min-anchor-weight 0.75`. The CLI prints *only* the proxy schema/output SHA-256 and vertex/triangle counts, not source geometry. The JSON result is a file, not a global Noosphere or simulator event. It is not a trained garment and has no implicit execution/attachment rights.

## Compatibility and remaining gates

- The glTF source geometry format is defined by the [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html). This importer is pure Python 3.11+ stdlib; no Khronos runtime code or licensed GLB assets are bundled. Preserve per-asset authorship and verify the license **outside** this parser before use.
- Tested offline with generated GLB byte fixtures only. Tests verify deterministic byte-for-byte output, untrusted inputs and no-clobber permissions. These fixtures are not a verified VRoid/real-person asset, cloth dynamics, GLB compression/sparse support, or a source of collision-body proxies.
- To integrate with #740, an independent reviewer must explicitly map `rig_version`, `body_profile`, asset digest/provenance and topology into the approved garment schema. Do not infer compatibility solely from string equality, and never hot-equip into MuJoCo without a safe model recompile/rebind boundary.
- Real garment GLB acceptance requires positive upstream/provenance checks, rig/age mapping, skin-transform/inverse-bind bake where needed, mesh simplification / body collision proxies, texture isolation, actual physics solver coupling, actual CPU/RSS and penetration benchmarks, independent exact-HEAD CI and manual scene QA. Do not merge or claim complete #376 until those gates pass.
