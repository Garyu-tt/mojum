# External world synthetic observation admission R0.1 (#7)

This opt-in Python stdlib-only adapter admits **offline, PUBLIC_SYNTHETIC**
observations from a caller-provided VR/game fixture. It never connects to an
engine, network, device, host filesystem, or Noosphere/Aquarium journal. It
cannot authenticate an engine's identity, grant real capabilities, or establish
physical contact. ExternalFrameGrant is trusted caller configuration, not a
credential. A source_revision and SHA-256 receipt are reproducibility
fingerprints, NOT signatures or evidence of truthful measurements.

## Example

    from mojum.world_external_frame import (
        ExternalFrameGrant, FRAME_SCHEMA, CAPABILITY,
        admit_external_frame, verify_external_admission,
    )
    grant = ExternalFrameGrant("game_fixture", "public.demo", "level01", 20_000_000)
    frame = {
        "schema": FRAME_SCHEMA, "audience": "PUBLIC_SYNTHETIC",
        "synthetic": True, "source_kind": "game_fixture",
        "source_id": "public.demo", "world_id": "level01",
        "capability": CAPABILITY, "source_revision": "ab" * 32,
        "sequence": 0, "tick": 0, "time_ns": 0,
        "objects": [{"id": "toy", "x_m": 1.5, "y_m": 2, "visible": True}],
        "physical_contact": False, "physical_actuation": False,
    }
    admission0 = admit_external_frame(frame, grant=grant, expected_tick=0)
    assert verify_external_admission(admission0, grant=grant, expected_tick=0) == admission0
    frame1 = {**frame, "sequence": 1, "tick": 1, "time_ns": 20_000_000}
    admission1 = admit_external_frame(frame1, grant=grant, expected_tick=1,
                                      previous_admission=admission0)

## Wire constraints

Exact versioned object keys; PUBLIC_SYNTHETIC only; explicit
world.observe.synthetic capability; source_kind game_fixture or vr_fixture;
1..1,000,000,000 ns integer tick period; integer logical ticks/sequence
0..1,000,000,000; exact time_ns = tick * tick_period_ns; 64 lowercase
hexadecimal source revision; at most 64 uniquely identified points with
finite x/y coordinates in [-10,000, 10,000] metres and boolean visibility.

Unknown keys, nonfinite numbers, bool-as-int, subclasses, Unicode identifier
spoofing, mismatched source/world/capability, clock drift, wrong sequence,
and modification of previous admission are rejected. Genesis requires
tick=sequence=0. Further frames require precisely the preceding local
admission, incrementing tick and sequence by one, plus validated SHA-256
admission digest chaining.

The returned admission is detached JSON-safe data with frame_sha256,
parent_sha256 and admission_sha256. The receiver must maintain its own
trusted genesis anchor and monotonic cursor. A malicious source able to
fabricate a full chain cannot be stopped cryptographically by plain hashes.
This provides neither signing nor durable anti-replay state. Real-world
adapters need separate authentication, privacy consent, resource caps,
replay persistence, and capability enforcement.

No integration with world_room.advance or world_physics_barrier is implicit:
this is external observation only, never actuator intent or authoritative
world state. The canonical point-room and existing startup paths are unchanged.

## Focused tests

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_external_frame.py' -v
