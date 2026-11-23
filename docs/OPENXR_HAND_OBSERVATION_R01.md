# OpenXR hand tracking: isolated read-only observations (R0.1)

Issue [#755](https://github.com/Garyu-tt/mojum/issues/755). Parents: #7 (external worlds), #6 (neural observations), #5 (virtual body).

**Scope:** opt-in offline, stdlib-only XR_EXT_hand_tracking hand joint decoder and read-only observation hash chain. It **does not** connect to a headset, import an OpenXR SDK, write Noosphere journals, alter the Aquarium, transform joints to the Dante rig, or generate motor commands. All source data remains **untrusted** until a separate origin/authentication/capability gate verifies it.

## Wire format and mapping

Input schema: **mojum.openxr.hand-locations/1**. Required exact keys: schema, extension (XR_EXT_hand_tracking), hand (left/right), base_space (local/stage), calibration_sha256 (lowercase 64-character hash), frame_index, time_ns, is_active and joints.

The joints field must be an ordered list of **26** records, matching Khronos XrHandJointEXT 0..25. Each record has exact fields: joint, location_flags, position_m, orientation_xyzw and radius_m. The 26 names are provided by **mojum.openxr_hand.JOINTS**.

XrSpaceLocationFlags bits are 0x01 orientation valid, 0x02 position valid, 0x04 orientation tracked and 0x08 position tracked. Valid is **not** the same as tracked. An active hand requires valid position and orientation for all 26 joints, but tracking bits may be unset. An inactive hand requires 26 entries with zero flags and null pose/radius. Position is in meters; orientation is a quaternion **x,y,z,w**. The input module never extrapolates missing poses or invents contact.

Only declared OpenXR local/stage reference spaces are admitted; the provided calibration hash is **not** an authentication or a verified real-world transform. No VIEW-space values, implicit origin changes or cross-hand stream synchronization. Mojum safety-admission limits, not OpenXR requirements: coordinates within ±1000 m, radius 0..0.2 m, quaternion squared length within 0.01 of 1, nonnegative signed 64-bit clocks and indexes, and 64 KiB maximum JSON bytes. Duplicate JSON keys, NaN/Inf, unknown fields, reordering or duplication of joints, and invalid flag combinations are refused.

## Offline synthetic example

~~~python
from mojum.openxr_hand import HandStream, JOINTS, INPUT_SCHEMA, EXTENSION, ZERO_HEAD

frame = {
    "schema": INPUT_SCHEMA, "extension": EXTENSION,
    "hand": "left", "base_space": "local",
    "calibration_sha256": "a" * 64,
    "frame_index": 0, "time_ns": 1_000_000, "is_active": True,
    "joints": [{
        "joint": name, "location_flags": 0x03,  # valid; not necessarily tracked
        "position_m": [0.0, 0.0, 0.0],
        "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
        "radius_m": 0.01,
    } for name in JOINTS],
}
stream = HandStream(hand="left", base_space="local", calibration_sha256="a" * 64)
observation = stream.ingest(frame, expected_head=ZERO_HEAD)
assert observation["external_actuator"] is None
~~~

The bounded JSON byte parser is **stream.ingest_json(bytes, expected_head=...)** and rejects duplicate keys. The output schema is **mojum.sensor.openxr-hand/1**, includes exact provenance **untrusted-openxr-readonly/1**, previous_sha256 and sha256. Separate stream objects are required per hand, base space and calibration revision. Timestamps and indexes must strictly increase; dropped samples are allowed. A failed input never advances the local stream head. Hash-linking detects edits but **is not a digital signature, identity proof, clock attestation or proof of informed consent**.

The optional **verify_observation_chain** checks a bounded list (maximum 4096) without an XR SDK. The previous hash must be anchored independently by a trusted subsystem before relying on it. The returned observation is detached from caller-owned lists; modifying it cannot alter internal stream state.

## Offline test and observed cost

~~~sh
PYTHONPATH=src python -m unittest tests.test_openxr_hand -v
~~~

20 local Python 3.11 tests passed, covering replay, inactive joints, valid-but-untracked flags, invalid quaternions, stale head, nonmonotonic timestamps, malformed/duplicate JSON, hand/reference/calibration switching, tamper detection, oversized inputs and mutation isolation. A separate 1,000-frame 26-joint synthetic benchmark with tracemalloc measured **1.929 ms per frame** during ingest and **12.22 MiB peak** traced allocations including 1,000 retained output packets and full-chain verification. Target for further testing: at most 5 ms/frame and 16 MiB/1,000 retained packets on reference CPU, subject to repeated independent measurements. These are **not** hardware XR/Android results.

## Upstream version/license gate and unfinished integration

Protocol reference: Khronos [OpenXR specification release 1.1.65](https://registry.khronos.org/OpenXR/specs/1.1/html/xrspec.html), **XR_EXT_hand_tracking**, XrHandJointEXT and XrHandJointLocationsEXT. Optional future SDK: [KhronosGroup/OpenXR-SDK](https://github.com/KhronosGroup/OpenXR-SDK). The repository LICENSE is Apache 2.0, with certain SDK header and loader files additionally offered under an Apache-2.0 OR MIT expression per [COPYING.adoc](https://github.com/KhronosGroup/OpenXR-SDK/blob/main/COPYING.adoc). The published OpenXR specification is licensed separately. **No external code, binary, models, assets or new dependency are included in this slice**; license review remains mandatory before a real XR runtime adapter.

Not implemented: xrCreateHandTrackerEXT/xrLocateHandJointsEXT polling; enabling the extension and Android/desktop permissions; per-device calibration; time-alignment with canonical world/body revisions; sensor consent and privacy retention; authenticated source signatures; GenESIS sensor port integration; real-body or virtual-body actuation. These require separate independently tested and reviewed PRs. Never call this fixture a live OpenXR integration or use hand observations to drive hardware.
