# R0.1: synthetic object-room trace attestation

Parent: [world simulator #7](https://github.com/Garyu-tt/mojum/issues/7). Separate from the point-only trace #808, object state machine #839 and canonical Aquarium persistence. This is an offline, deterministic, read-only check of synthetic fixtures. It does not prove physical contact or authentic origin.

## Trace contract

The exact JSON envelope is mojum.world.object-trace/1 with audience PUBLIC_SYNTHETIC. It contains room geometry, starting coordinates, canonical sorted starting objects, a list of fully observed steps, final room/object revisions, and a trace_sha256 content checksum.

Each step contains one canonical command {dx,dy,action,object_id,place}, the room event, object event, room state and objects state. The inspector replays every step through the existing world_objects.advance_objects and world_room.advance, then compares canonical serialized JSON bytes, including numeric type distinctions. Recomputed hashes never excuse a forged event. All transitions use synthetic room/object compare-and-swap revisions.

Limits: 128 steps, 128 obstacles, 16 objects, 10,000m synthetic coordinate maximum, 1,000m per command coordinate component and 256KiB serialized trace. Raw JSON decoding refuses duplicate object keys and nonfinite constants. Parsing is bounded; unknown fields, malformed types, bool-as-number, wrong audience, reordered/omitted/fabricated steps and stale revisions fail closed. Direct Python `record_trace` command dictionaries also reject non-native string keys, including equality/hash-spoofed objects and `str` subclasses; strict decoded JSON input already applies a native-key gate.

Content hashes are NOT signatures or proof of trusted origin: a malicious author controlling the entire fixture may make another consistent trace. The tool is not an actuation authority, a Noosphere journal or a policy learning component.

## Python API

    import json
    from mojum.world_room import Room
    from mojum.world_object_trace import record_trace, attest_trace
    room = Room(3, 3, ((2, 1, .1, 1),))
    trace = record_trace(room, 1, 1, [("cube", 1.1, 1.1)], [
        {"dx": 0, "dy": 0, "action": "pick", "object_id": "cube", "place": None},
        {"dx": .2, "dy": 0, "action": "wait", "object_id": None, "place": None},
        {"dx": 0, "dy": 0, "action": "place", "object_id": "cube", "place": [1.2, 1.2]},
    ])
    print(attest_trace(trace))
    with open("public_object_trace.json", "w", encoding="utf-8") as handle:
        json.dump(trace, handle, sort_keys=True)

## Read-only CLI and tests

    PYTHONPATH=src python -m mojum.world_object_trace public_object_trace.json
    PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_object_trace.py' -v

On success the CLI emits a sorted JSON attestation with verified step count, both terminal revisions, checksum, physical_contact=false, and authority=offline_synthetic_fixture_only. A rejected file exits 2. It does not rewrite input files, Aquarium SQLite or PRIVATE Noosphere data.

## Future integration

Explicitly authorized, redacted, PUBLIC_SYNTHETIC fixture exports may be checked before offline visualization. Do not interpret fixture checksums or synthetic provenance as verified real-world observations or as permission to execute motor commands.