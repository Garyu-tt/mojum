# Cloth snapshot/replay codec R0.1

## Purpose

`mojum.cloth_snapshot` provides an opt-in, standard-library-only serialization contract for explicitly **PUBLIC_SYNTHETIC** cloth-proxy fixtures. It is a data-integrity and replay-continuity helper, not a physics engine or a production checkpoint service.

## Wire contract

A canonical ASCII JSON envelope contains:

- schema `mojum.cloth-snapshot/1`;
- tick, integer nanosecond timebase and fixed `dt_ns`;
- SHA-256 mesh identity, rig revision, garment/profile revision and solver identifier;
- equal-length arrays of 3D positions and velocities, with at most 4096 vertices;
- explicit `PUBLIC_SYNTHETIC` provenance and `consent: true`;
- SHA-256 over the normalized state payload.

Numbers must be finite native integers/floats (booleans are rejected), coordinates/velocities are bounded to ±1,000,000, and `time_ns == tick * dt_ns`. Unknown/missing fields, duplicate JSON keys, non-canonical JSON, malformed digests, count mismatches, oversized payloads and digest tampering fail closed.

## API

- `encode_snapshot(state) -> bytes`: validate and emit canonical envelope.
- `decode_snapshot(data) -> ClothSnapshot`: validate canonical representation, schema and digest; return detached immutable tuples.
- `verify_replay(sequence) -> tuple[str, ...]`: require 1..10,000 snapshots with contiguous ticks and identical timebase, mesh, rig, profile, solver and vertex count.

Resource limits: each snapshot is at most 1 MiB; replay admission checks a 16 MiB aggregate byte budget before decoding any entry. Oversized text is rejected by character-count preflight before ASCII encoding.

## Run

```sh
python -m unittest discover -s tests -p 'test_cloth_snapshot.py' -v
```

## Explicit limits

- No filesystem, network, subprocess, model loading, GLB parsing, solver stepping or runtime/Aquarium wiring.
- No PRIVATE Noosphere data, canonical journal writes, actuator authority or safety claims.
- A valid digest proves only consistency with the supplied bytes, not authenticity, provenance, durability, physical correctness or deterministic equivalence of an external solver.
- The codec stores caller-supplied positions and velocities; it does not reconstruct or advance simulation state.
- Snapshot/replay is not integrated into the live cloth module. This slice is independently opt-in and additive.
