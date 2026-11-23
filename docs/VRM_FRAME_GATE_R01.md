# VRM pose frame admission gate R0.1

## Purpose

`PoseFrameGate` is a small CPU/stdlib-only admission boundary for a future read-only VRM renderer. It accepts an already-normalized pose packet and returns a detached immutable receipt. It does not render, interpolate, generate motion, load an avatar, or issue motor commands.

## Packet contract

The input is an exact plain Python dictionary with exactly these keys:

- `schema`: `mojum.vrm-pose-frame/1`
- `epoch`: 1..96 ASCII identifier characters (`A-Z a-z 0-9 . _ : -`)
- `sequence`, `tick`, `timestamp_ns`: nonnegative plain integers no greater than signed 63-bit max
- `body_revision`: lowercase 64-character SHA-256-shaped revision identifier
- `joints`: 1..configured-max exact joint-name keys, each mapped to three finite numeric coordinates

The joint inventory defaults to at most 128 entries (hard configuration ceiling 512). Coordinates default to an absolute limit of 10,000 units (hard configuration ceiling 1,000,000). Names are bounded ASCII identifiers. Booleans, numeric subclasses, NaN, infinity, oversized integers and unknown fields are rejected.

## Temporal rules

A caller must explicitly call `begin_epoch(epoch=..., body_revision=...)` before admitting frames. Within that epoch, sequence, logical tick and source timestamp must each increase strictly. Gaps are allowed: the gate does not claim a fixed frame rate or reconstruct missing frames.

A changed body revision is rejected. The caller must explicitly start a new, different epoch with the new revision; this clears the previous monotonic counters. Reusing the same epoch for reset is rejected.

Rejected frames do not advance any counter. Accepted joint data is sorted by name, copied into immutable tuples, and canonicalized before its SHA-256 receipt digest is calculated. The digest detects accidental changes to the normalized packet representation; it is not a signature, source authentication, or proof that a producer told the truth.

## Privacy and safety boundary

Every admission requires `consent=True` and exact source tag `PUBLIC_SYNTHETIC`. This is a caller assertion, not a consent-management system or provenance authentication. The module has no filesystem, network, SQLite, Noosphere, MuJoCo, VR SDK, renderer, or device-control access. Do not feed PRIVATE records or physical sensor data through this fixture gate.

The returned coordinates remain visualization input only. They are not authoritative body state, calibrated anatomy, physical contact, safe motor targets, or evidence of real-time rendering performance.

## Verification

Run the focused offline suite from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_vrm_frame_gate.py' -v
python -m compileall -q src/mojum/vrm_frame_gate.py tests/test_vrm_frame_gate.py
```

The tests use synthetic in-memory packets only. Before merge, require exact-HEAD Python 3.11–3.13 CI and independent review. This Draft PR is not a merge or release authorization.
