# Aquarium launch profile R0.1

```json
{
  "schema": "mojum.aquarium.launch-profile/1",
  "db_path": "./dante_PRIVATE.sqlite3",
  "ticks": 120,
  "neural_mode": "shadow",
  "motor_mode": "scripted",
  "brain_mode": "placeholder",
  "sensory_mode": "none",
  "temporal_mode": "none",
  "pulse_sum": "auto",
  "read_only": false
}
```

## Purpose

`mojum.aquarium_config.validate_profile()` validates a caller-supplied, flat launch profile before a separate caller decides whether to start the Aquarium. It is an opt-in data contract, not a launcher, configuration file loader, process manager or runtime integration.

The validator accepts either an exact built-in `dict` or JSON text. It returns an immutable, slotted `LaunchProfile` containing normalized scalar fields and a deterministic SHA-256 digest. The public value-object constructor repeats field and cross-field validation and verifies the digest, so directly constructing a profile cannot bypass preflight. The digest detects accidental changes; it is not a signature, authentication token, authorization grant or protection from rollback.

## Use

```python
from mojum.aquarium_config import validate_profile

profile = validate_profile({
    "schema": "mojum.aquarium.launch-profile/1",
    "db_path": "./dante_PRIVATE.sqlite3",
    "ticks": 120,
})
print(profile.sha256)
print(profile.runtime_options())
```

The schema is mandatory. `db_path` is mandatory and `room_file` is optional. Other fields receive the documented stable defaults shown below. Paths are inert caller-provided text: this module never opens, resolves, expands, creates, or checks them.

## Fields and limits

| Field | Type / accepted values | Default |
|---|---|---|
| `schema` | exact string `mojum.aquarium.launch-profile/1` | required |
| `db_path` | nonempty exact string, <=512 chars | required |
| `ticks` | exact integer, 0..1000; zero is reserved for `local_cell_v2_a_archive`, all other modes require 1..1000 | 120 |
| `dt_ns` | exact integer, 1000..1000000000 | 10000000 |
| `neural_mode` | `shadow`, `disabled` | `shadow` |
| `motor_mode` | supported Aquarium motor selector | `scripted` |
| `brain_mode` | `placeholder`, `developmental_controller` | `placeholder` |
| `sensory_mode` | `none`, `toy_probe`, `toy_probe_v2` | `none` |
| `temporal_mode` | `none`, `range_guided` | `none` |
| `pulse_sum` | `auto`, `ordered`, `compensated` | `auto` |
| `read_only` | exact boolean | `false` |
| `room_file` | null or nonempty exact string, <=512 chars | null |

Motor selectors accepted at R0.1: `scripted`, `developmental_fixture`, `local_cell_v2b`, `local_cell_v3`, `local_cell_v3_escape`, `local_cell_v4`, `local_cell_v2`, and `local_cell_v2_a_archive`.

Cross-field rules mirror the currently exposed Aquarium constructor constraints: developmental controller and legacy `toy_probe` require `developmental_fixture`; `toy_probe_v2` requires `developmental_fixture` or `local_cell_v4`; `local_cell_v4` rejects legacy `toy_probe`; `range_guided` requires `toy_probe`; explicit pulse summation requires neural shadow mode. The launch-profile preflight also mirrors CLI constraints: `local_cell_v2_a_archive` requires `ticks=0` (all other selectors require at least one tick), and fixed-geometry local-cell selectors (`local_cell_v2b`, `local_cell_v3`, `local_cell_v3_escape`, `local_cell_v4`, `local_cell_v2`, `local_cell_v2_a_archive`) reject `room_file`.

## Failure and trust boundaries

- Unknown fields, missing required fields, duplicate JSON keys, non-finite JSON constants, wrong root shape, oversized text, recursive parser failures, bool-as-int, numeric subclasses, hostile mappings, unsupported modes and incompatible combinations fail closed with `AquariumConfigError`.
- JSON text is capped at 16 KiB. The accepted profile is flat; nested objects/arrays are not valid field values.
- Path strings are checked only as bounded inert text. No path traversal, existence, permissions, symlink, or filesystem safety claim is made because no filesystem operation occurs here.
- The profile does not install packages, read environment variables, infer host settings, open SQLite, access PRIVATE Noosphere records, instantiate simulation code, write journals, or authorize physical output.
- No defaults are silently applied to an existing journal. A caller must still pass matching options to the actual runtime and obey that runtime's own admission/replay checks.
- This implementation does not modify the Aquarium CLI, portable host, canonical manifest, journal schemas or policy identity.

## Reproduction

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_aquarium_config.py' -v
```

The validator is stdlib-only and targets Python 3.11+. Independent exact-HEAD CI and QA are required before merge.
