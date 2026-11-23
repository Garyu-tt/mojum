# Opt-in garment manifest and wardrobe CAS contract R0.1 (#376)

**Status: experimental data boundary, not a cloth engine or clothing release.**
A synthetic cloth manifest and revision-guarded wardrobe transition module now exists in
src/mojum/garment_contract.py. It does **not** implement MuJoCo flex, Jolt SoftBody,
PBD, GLB skinning, collision detection, self-contact, equip/unequip inside a running
3D simulation, real actuators, motor safety, or a persistent authoritative journal.

## Reproduce offline (no new dependency)

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_garment_contract.py' -v
~~~

Eight deterministic unittest methods include malformed JSON and manifest fields,
path traversal, symlink rejection, changed GLB hashes, conflicting slots,
stale revisions, rig changes, age-range rejection, and fail-closed rebinding.
All synthetic mesh bytes are built by the tests. Baseline Python stdlib only,
no external assets, private data, network, renderer, or simulation process.

## Versioned data boundaries

- Garment: mojum.cloth.garment/1, exact keys:
  schema, garment_id, slot, source, mesh, rig, material, anchors.
- Source: origin (manual-export / procedural-synthetic),
  license_spdx, attribution. **A declared license string is not
  verified permission**. Review upstream assets/creators/transitive
  requirements independently before redistribution.
- Mesh: safe relative .glb path, SHA-256 hex, declared size,
  vertex count. verify_mesh_file() checks size, GLB2 header, digest
  and rejects symlinked path components. It does not parse skin data
  or attest semantic validity and is not a race-safe sandbox if an
  untrusted writer can change files concurrently.
- Rig: skeleton_version, body_profile, age_min, age_max
  (currently synthetic 6..16 integer years). It is independent of
  the agent's genotype, memory or development journal.
- Material: *integer units* mass_g, thickness_um,
  stretch_milli, bend_milli, damping_milli (indices 0..1000
  are descriptive until a real solver defines their meaning).
  They are **not physically calibrated**.
- Anchors: one or more vertex_index, joint_id, weight_milli,
  every anchored vertex sums to exactly 1000 and has <=4 distinct
  joints. Joint existence, skinning/vertex bounds in the actual
  geometry and skeletal transform accuracy need the future engine.
- Wardrobe: mojum.cloth.wardrobe/1, exact keys
  schema, revision, rig, worn. Pure JSON-shaped detached copies,
  no SQLite writes. Allowed slots: head, neck, torso, outer, legs,
  hands, feet. One garment per slot, no implicit replacement.

Use new_wardrobe(), equip(), unequip(), rebind_wardrobe().
Each transition requires an exact expected_revision and produces
a *new* state with revision +1. Rebinding verifies every worn
garment against a supplied manifest at the same SHA-256 and refuses
incompatible age/profile/skeleton changes rather than silently
dropping clothing. This guards stale in-memory proposals, **not**
a multi-process or distributed CAS without an authoritative store.

## CPU / memory bounds

- Manifest read <=32 KiB, declared GLB <=32 MiB, <=4096
  vertices and <=256 anchor records. Verification streams GLB
  SHA-256 in 64 KiB chunks; it never loads mesh bytes as a model.
- Wardrobe <=12 serialized records (one per allowed slot),
  revisions <=2^63-1. All schema fields fail closed on unexpected
  keys and bool-as-int; input is detached before any state change.
- Isolated local tests completed on Python 3.13.5 with all 7
  original methods passing. An eighth regression guards exact schema string
  discrimination; final-HEAD GitHub CI evidence is required for the combined suite. No MuJoCo/Jolt/Android benchmark or latency
  measurements claimed.
- Feature is opt-in by import. No change to Aquarium, default
  package dependencies, canonical Graph IR or private Noosphere.

## Next integration gates

1. Bind declared joint IDs to a **verified** actual rig; independently
   verify glTF geometry, skin weights, mesh licensing and revisions.
2. Compare against #376 MuJoCo flex spike and #702 Jolt/PBD research;
   benchmark identical synthetic scarf/panel, SI units, p50/p95
   CPU ms/tick, RSS, drift, penetration and contact semantics.
3. Implement one-way visual cloth adapter behind a safety-isolated
   failure boundary. A solver crash must not affect organism
   memory, physical controls or the synthetic Aquarium.
4. Independently review exact PR HEAD/CI before merge. Avoid
   describing this as a 3D outfit, realtime physics, body
   feedback, or hot-equip without recompilation.

Upstreams (only referenced, **not vendored or imported**):
MuJoCo flex (Apache-2.0): https://github.com/google-deepmind/mujoco
Jolt SoftBody (MIT): https://github.com/jrouwe/JoltPhysics
PositionBasedDynamics (MIT): https://github.com/InteractiveComputerGraphics/PositionBasedDynamics
Actual assets each require their own license/provenance review.
