# MuJoCo model-complexity budget preflight, R0.1

Issue #1010; parent #689. This is a **metadata-only admission check** for a future optional MuJoCo model. It does not load XML, import MuJoCo, run a simulation, or change the canonical Aquarium.

## Contract

Call `audit_model_budget(metadata, profile="cpu_small")` from `mojum.mujoco_model_budget`. The exact input schema is `mojum.mujoco-model-metadata/1` and contains `engine_version`, a lowercase SHA-256 `model_sha256`, and exact nonnegative integer counts for MuJoCo model objects (`nbody`, `njnt`, `ngeom`, `nsite`, `nsensor`, `nactuator`, `ntendon`, mesh counts and flex counts). The caller must derive the digest from the exact model source bytes and obtain counts from a separately trusted engine/parser boundary.

The validator rejects metadata dictionaries with a key count different from the fixed schema before enumerating keys. The `engine_version` field is limited to 128 characters to keep the canonical report bounded.

Two built-in count-only profiles are available:

- `cpu_small`: conservative small-body-fixture limits; flex and actuators are disallowed.
- `cpu_standard`: larger research-fixture limits, including bounded actuators/flex metadata.

The report returns all per-field violations in stable field order, a canonical SHA-256 of the report body, and an explicit status. Counts at the limit are admitted; any field above its limit marks the report `over_budget`. Invalid metadata is rejected rather than partially summarized.

## Security and interpretation

- Input values are caller assertions, **not authenticated MuJoCo introspection**. A model digest is only useful when independently computed over the exact source bytes.
- `within_budget` means only that declared counts fit this named static policy. It does not estimate CPU time, memory, solver stability, collision correctness, real-time capability or safe physical motion.
- `authorizes_execution` is always `false`; `requires_native_engine_validation` is always `true`. The result is not an actuator permit or a model trust decision.
- The module has no filesystem, network, native extension, SQLite, Noosphere or Aquarium access and adds no dependency.
- Profiles are repository policy defaults, not universal capacity claims. Owners must adjust them against measured CPU/RAM data from the intended host before production use.

## Reproduce

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mujoco_model_budget.py' -v
```

The test suite covers exact thresholds, every-resource violations, malformed types/schema/digests, cardinality and version-length limits, cross-field mesh/flex consistency, deterministic report hashing, and detached input/output behavior. This remains an opt-in preflight utility; independent review and exact-head CI are required before merge.
