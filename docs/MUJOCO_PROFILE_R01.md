# MuJoCo CPU execution profile R0.1

This document describes a **preflight-only** configuration contract for optional
CPU simulation. It does not start MuJoCo, load an MJCF file, or grant actuator
authority. The core Mojum package remains standard-library-only.

## Contract

Schema: `mojum.mujoco.cpu-profile/1`.

`validate_profile(mapping)` accepts one exact native `dict` with these fields:

| Field | Type | Bound / allowed values |
| --- | --- | --- |
| `schema` | string | exactly `mojum.mujoco.cpu-profile/1` |
| `enabled` | bool | explicit opt-in intent; false remains false |
| `timestep_ns` | int | 1,000 to 100,000,000 ns |
| `steps` | int | 1 to 10,000,000 |
| `warmup_steps` | int | 0 to 1,000,000 and no more than steps |
| `cpu_threads` | int | 1 to 16 |
| `integrator` | string | `euler`, `implicit`, `implicitfast` |
| `solver` | string | `newton`, `cg` |

The integrator and solver fields are independently allowlisted; this validator does not infer a compatibility rule absent an explicit pinned MuJoCo version/model contract. Direct native mappings are rejected by field cardinality before key enumeration, and schema/option strings are length-checked before allowlist hashing. The total requested simulated duration is capped at one day. Every integer
field requires an exact built-in `int`; booleans and floats are not accepted as
integers. Missing or unknown keys, subclasses, unsupported values and invalid
cross-field combinations fail closed with `ProfileError`.

`loads_profile(str_or_bytes)` accepts UTF-8 JSON up to 8 KiB, rejects duplicate
keys, non-finite constants, invalid UTF-8 and nesting deeper than eight levels.
The parsed object goes through the same semantic validator.

The returned frozen `CpuProfile` contains normalized scalar fields and a
SHA-256 digest. `to_mapping()` returns a fresh detached mapping. The digest is
only a deterministic consistency fingerprint; it is not a signature,
authentication token, anti-rollback anchor or proof of solver behavior.

## Example

```python
from mojum.mujoco_profile import validate_profile

profile = validate_profile({
    "schema": "mojum.mujoco.cpu-profile/1",
    "enabled": True,
    "timestep_ns": 2_000_000,
    "steps": 1000,
    "warmup_steps": 50,
    "cpu_threads": 2,
    "integrator": "implicitfast",
    "solver": "newton",
})
print(profile.digest)
```

Run the hermetic tests from the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_mujoco_profile -v
```

## Explicit limitations

- The contract is not wired into an Aquarium runner or MJCF compiler.
- It performs no XML parsing, filesystem/network access, MuJoCo import, stepping,
  benchmarking, memory isolation or OS-level CPU scheduling.
- A syntactically valid profile does not prove that a particular model compiles,
  remains stable, is deterministic across platforms, or meets wall-time limits.
- The profile grants no motor/actuator permission and makes no claim about
  physical movement, calibrated anatomy or hardware safety.

This is an additive offline contract for issue #1131 under parent #689.
