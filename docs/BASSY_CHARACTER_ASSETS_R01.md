# Mojum R0.1 Bassy character asset manifest (issue #149)

This is an **offline, optional visual-asset contract**, not a character generator, renderer, rig verifier, morph engine, neural/body backend or evidence of a running Dante. It adds no runtime dependency on Bassy, network, GPU or any proprietary asset. No Bassy code, models or example exports are committed.

## Contract

\`mojum.character-assets/1\` is a single JSON file. Required top-level fields:

- \`schema\`: \`mojum.character-assets/1\`
- \`organism_id\`: stable, human-provided ASCII identifier (not a display name and never derived from genome)
- \`origin\`: \`manual-bassy-export\`
- \`credits\`: exactly \`{"text":"Made with Bassy","url":"https://bassycharactercreator.itch.io/bassy-character-creator"}\`
- \`forms\`: 1-16 visual age keyframes, age 6 through 16. Each contains:
  - \`age_years\`: integer keyframe; unique per manifest
  - \`glb_path\`: unique relative POSIX path under the caller-provided asset root, with \`.glb\` suffix
  - \`sha256\`: lowercase SHA-256 digest of the actual GLB file
  - \`bytes\`: exact GLB file size (20 bytes to 64 MiB)
  - \`height_mm\`: declared visual height (300-2500 mm), **not** physical body measurement
  - \`skeleton_version\`: supplied rig contract label, **not** a verified rig equivalence certificate
  - \`lod\`: integer 0-3 (reserved for downstream viewers; currently one keyframe per age)

No implicit assets, embeddings, extra fields, duplicate ages or duplicate paths are accepted. Missing/malformed Bassy attribution is rejected. Review the original app's licensing/credits before redistributing actual models; this contract **does not grant** redistributable rights.

## Preparation and usage

Create the four manually exported Bassy keyframes for 6, 9, 12 and 16 when the editor permits. Put them under a local private asset directory. For each file populate \`bytes\` and \`sha256\` from its actual on-disk bytes (e.g. \`stat\` and \`sha256sum\` on Linux). No binary sample is included, and declared rig/version compatibility must not be guessed.

\`\`\`python
from mojum.character_assets import load_manifest, select_form, verify_files

manifest = load_manifest("private/character.json")
verify_files(manifest, "private/assets")
form = select_form(manifest, visual_age_years=12.5)
print(form["age_years"], form["glb_path"], manifest["credits"])
\`\`\`

\`select_form\` chooses the newest available keyframe at or below the requested visual age, clamped to the first/last keyframe. It **always swaps whole assets** and never interpolates rigs, morphs geometry or mutates the organism. After reaching the age-16 keyframe, visual ageing stops; elapsed time, learning, memory and GENESIS state are separate and unchanged.

\`verify_files\` rejects path traversal and path-component symlinks, validates expected file lengths and SHA-256, and checks a bounded GLB v2 header plus first glTF JSON chunk. This is **basic structural/integrity verification**, not proof that joints, skin weights, animations or geometry will render. It is not a race-safe filesystem sandbox against a malicious process modifying paths concurrently; use trusted private asset storage.

## Scope still required to close #149

1. Actual licensed Bassy GLB exports and provenance/preset archives for two or more visual ages.
2. A standalone, offline GLB viewer that displays Bassy attribution and handles rig/height transitions safely.
3. An integration test showing persisted Mojum memory and identity survive an actual visual-age swap.
4. A compatible retargeting/morph strategy **only after** comparing real rigs and topology.

Run focused CPU-only tests:

\`\`\`sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_character_assets.py' -v
\`\`\`
