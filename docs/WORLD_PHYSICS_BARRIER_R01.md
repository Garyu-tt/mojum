# World / virtual-body logical-tick barrier R0.1 (#7)

**Status:** opt-in isolated CPU/stdlib fixture, not wired into Aquarium, MuJoCo,
external VR/game systems, hardware, PRIVATE journals, or GENESIS policy.

The new mojum.world_physics_barrier module takes an already-validated
world_room.RoomState and an existing mojum.virtual-physics-observation/1
**passive synthetic** physics packet. It refuses a pair unless:

1. The room geometry, point position and RoomState revision recompute
   exactly, and the caller's independent expected room revision still matches.
2. The physics packet passes its own bounded schema validator; the caller's
   independently pinned expected body digest still matches its content.
3. Both have exactly the same logical tick, with physics time_ns equal to
   tick * dt_ns (integer only; 1,000 through 1,000,000,000 ns per step;
   int64 timestamp overflow rejected). The host wall clock is not consulted.
4. If supplied, the prior checksummed receipt is internally well-formed and
   has the immediately preceding tick, equal dt_ns, the same room-geometry
   digest and the same physics backend. Gaps, duplicates, rollback and silent
   room/engine changes fail. An absent prior receipt means a **standalone
   starting point**, not verified proof of a historical prefix.

The output is a new detached mojum.world-physics.barrier/1 receipt with both
revisions, immutable geometry digest, synthetic backend name, logical clock,
optional previous checksum and a deterministic SHA-256 checksum (commit).
verify_barrier_receipt verifies **shape and self-consistency only**. A forged
input and recomputed hash will also pass: this is **not a signature, proof of
physical origin, cross-process writer lease, authentication, external engine
verification, or durable commit**. To use across a cold restart, independently
anchor the previous receipt's checksum to a trusted journal/manifest; this
module does not perform the anchoring itself. No private observations are
logged or exported by this module.

Synthetic usage:

~~~python
from mojum.world_physics_barrier import (
    align_world_physics_frame, body_frame_revision,
)

receipt = align_world_physics_frame(
    room, state, passive_physics_packet,
    dt_ns=1_000_000,
    expected_room_revision=trusted_room_revision,
    expected_body_revision=trusted_body_revision,
    previous=trusted_previous_receipt,  # None when starting a new chain
)
~~~

The expected body checksum can be computed by body_frame_revision(packet)
at the **trusted measurement boundary**, then pinned and compared later.
Simply hashing the packet immediately before calling the barrier does not
establish external authenticity. physical_contact and physical_actuation must
both be False; virtual contacts are synthetic, not hardware readings.

Offline tests:

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p test_world_physics_barrier.py -v
~~~

The existing world trace (#808) and four-ray range sensor (#812) are unrelated,
separately owned features; no direct integration is implied.
