# FingerSense synthetic ingress R0.1

`mojum.fingersense-frame/1` is a bounded, opt-in admission contract for caller-supplied synthetic fingertip samples. It is not a device driver and does not establish that a physical contact occurred.

## Scope

- Explicit `PUBLIC_SYNTHETIC` visibility and explicit consent are mandatory.
- Source identity, source revision, contiguous sequence and strictly increasing integer tick are retained.
- Each frame contains 1–64 unique finger/channel samples. Values are fixed-point integers in `[0, 1_000_000]` milli-units.
- Inputs require exact native Python types and exact object keys; booleans are not accepted as integers.
- Admitted frames and source cursors are frozen detached dataclasses. A canonical SHA-256 digest is deterministic over normalized sample ordering.
- A source revision or identity change requires a new stream; this API intentionally does not guess whether a reset is legitimate.

## Example

```python
from mojum.fingersense_ingress import admit_frame

frame, cursor = admit_frame({
    "schema": "mojum.fingersense-frame/1",
    "visibility": "PUBLIC_SYNTHETIC",
    "consent": "explicit",
    "source_id": "fixture-hand-left",
    "source_revision": 1,
    "sequence": 0,
    "tick": 10,
    "samples": [
        {"finger_id": "index", "channel_id": "pressure", "value_milli": 125000},
    ],
})
next_frame, cursor = admit_frame(next_input_frame, cursor)
```

## Deliberate limitations

This module performs no USB, camera, haptic or other hardware I/O; no sensor calibration; no object/material/world inference; no anatomical or biological validation; no persistence; no network access; and no actuation or MotorGate integration. The digest is an integrity fingerprint, not a signature, authenticity proof or consent record. A caller must separately define and verify sensor units/calibration before translating any real device output into this synthetic contract.

## Verification

Run `python -m unittest tests.test_fingersense_ingress -v`. The dedicated workflow runs the focused tests on CPython 3.11, 3.12 and 3.13.
