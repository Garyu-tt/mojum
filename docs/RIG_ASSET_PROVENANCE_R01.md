# Virtual rig asset provenance / integrity R0.1 (issue #906, parent #5)

`mojum.rig_asset_provenance.verify_rig_assets` checks **offline, in-memory
manifest consistency** before a future reviewed body importer consumes a rig.
It does not import MJCF, activate MuJoCo, mutate the Aquarium/Noosphere,
fetch source code, open files, enable actuators, or authorize hardware use.

## Contract

Schema: `mojum.virtual-rig-provenance/1`. The complete manifest contains
exactly `schema`, `classification`, `source`, `joints`, `assets` and
`calibration_evidence_asset`.

- `classification`: `synthetic_unverified` or `calibration_claim_unverified`.
  Both are **self-declarations**. The latter must reference a supplied asset,
  but its contents and truth are **not** physically validated.
- `source`: `{repository: "owner/repo", revision: "40 lowercase hex",
  license_spdx: "SPDX identifier"}`. Source origin and rights are *declared*,
  not proven by these fields. A downstream importer must verify upstream
  provenance, applicable license, and consent independently.
- `joints`: exactly 53 objects `{id, parent, dof}` in root-first parent-before-
  child order; unique ASCII IDs, one root, each DOF in `1..6`, total exactly 85.
  This is a typed inventory fixture **not a claim that Dante's calibrated
  geometry or anatomy has been reconstructed**.
- `assets`: 1..16 entries `{path, bytes, sha256}` with unique flat
  `assets/<filename>` paths, lowercase SHA-256 digests, individual cap 8 MiB
  and total cap 32 MiB. No absolute paths, separators, `..` or implicit files.
- `calibration_evidence_asset`: `null` for synthetic declarations. For a
  calibration *claim* it must name one of the verified assets. It does not turn
  that declaration into established calibration.

Supply an exact Python `dict[str, bytes]` for **all** listed assets and no
other keys. Each value is immutable `bytes` with the declared length and
matching SHA-256. The function returns a deterministic receipt with manifest
and ordered inventory digests, counts, byte total and an explicit
`authorization: none_integrity_only` field. No signed or authenticated
attestation is created, and the result is **not** sufficient to execute motors.

## Smoke test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_rig_asset_provenance.py' -v
```

## Integrator contract

1. Obtain reviewed assets outside this module, with a provenance review and
   independently checked license. Do not trust unreviewed ZIP archives.
2. Supply the exact, size-limited immutable bytes and manifest for validation.
3. Use the receipt only as a consistency and hash pin. A future importer must
   verify MJCF semantics, model/geometry mapping, allowed joint dynamics,
   independent physical calibrations, permission scopes and actual engine
   observation feedback before enabling any *virtual* actuation.
4. Never treat a passing receipt as human anatomical calibration, source
   authentication, digital signature, motor gate approval, 5-second standing
   stability or perturbation recovery.

`#689`/PR `#743` retains exclusive ownership of opt-in MuJoCo integration.
This module is standalone and does **not** alter that branch or `main`.
