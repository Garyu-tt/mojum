# Offline world-room replay trace (issue #7, experimental)

\`mojum.world_trace\` is a **separate opt-in, stdlib-only** recorder/verifier for
\`mojum.world_room.Room\`. It is not the canonical Aquarium/Noosphere SQLite
journal, the separate PRIVATE \`aquarium_trace_export\`, MuJoCo, a signed record,
or evidence of real physical contact.

\`\`\`python
import json
from mojum.world_room import Room
from mojum.world_trace import make_trace, verify_trace_bytes

room = Room(4, 4, ((2.004, 1, .001, 2),))
trace = make_trace(room, (1, 2), ((.5, 0), (2, 0), (0, -.25)))
data = json.dumps(trace, separators=(",", ":"), allow_nan=False).encode()
state = verify_trace_bytes(data)
assert state.tick == 3
\`\`\`

To verify an explicitly supplied local trace file without writing to it:
\`python -m mojum.world_trace /path/to/synthetic-trace.json\`.
Success prints one JSON report with the final tick and revision; invalid
input returns exit code 1 and a JSON error. This command never creates a
world or journal from untrusted input.

The format \`mojum.world.point-room-trace/1\` pins room geometry, start,
ordered ticks and displacements, each prior CAS revision and full measured
synthetic observation, and the final revision. \`verify_trace\` starts from
tick zero and re-simulates *every* frame with the canonical swept AABB solver.
Missing ticks, fabricated contact, teleport positions, stale revisions,
partial/tampered observations, unknown fields, bool-as-number and non-finite
values fail closed. JSON duplicate keys also fail closed. Memory/CPU are
bounded by at most 1024 actions and 512 KiB of input; 4096 rectangles max.

**Trust boundary:** revisions are unkeyed deterministic SHA-256 values. A
malicious party can forge an internally self-consistent trace by recomputing
all frames. This proves replay consistency **only**, not authorship, wall-clock
time, historical authenticity, authorization or private journal continuity.
Retain independent authenticated anchors if a stronger provenance claim is
required. No live devices, networks, credentials or private journals are used.

Tests: \`PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_trace.py' -v\`.
