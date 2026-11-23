# VRM resource budget preflight R0.1

An offline, standard-library-only admission helper for optional VRM viewers. It consumes caller-supplied metadata; it does not parse GLB/VRM bytes, load textures, allocate GPU resources, inspect browser memory, or prove declared counts match an asset.

## Contract
- Schema identifier: moj um.vrm-resource-budget/1 (exact spelling is defined by the SCHEMA constant in the module).
- Exact fields: schema, vertex_count, index_count, material_count, texture_bytes, joint_count.
- Counts are native non-negative integers; booleans are rejected. Index count must be divisible by three.
- Encoded inventory is capped at 16 KiB; hard ceilings bound metadata and arithmetic.
- Estimated bytes = vertices × 32 + indices × 4 + texture bytes + materials × 256 + joints × 256. This is a planning heuristic, not measured renderer allocation.
- Defaults: 250k vertices, 1.5m indices, 64 materials, 64 MiB textures, 256 joints and 96 MiB estimated bytes.
- Above 80% of a configured limit warns; exceeding any limit rejects. A caller must not allocate on reject.
- Report contains counts, decision, estimate and canonical SHA-256 digest only.

## Safety and limitations
Caller owns metadata accuracy and asset provenance. Do not pass PRIVATE organism assets or user-specific paths. Compressed/transcoded GPU overhead is not inferred. This helper is advisory and opt-in, not a renderer sandbox, license verifier, or substitute for browser/mobile profiling.

## Verification
Run python -m unittest discover -s tests -p test_vrm_resource_budget.py on CPython 3.11–3.13. No third-party dependencies or network access.
