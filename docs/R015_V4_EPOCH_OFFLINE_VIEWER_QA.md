# R0.15: real epoch boundary in a read-only offline Aquarium viewer

**Draft QA only.** The original experimental source branch #132 remains
unmodified. This is playback of generated local PRIVATE synthetic data,
not an in-place memory migration or a production organism runtime.

## Contract

v4 has a separate persisted ABI:
- Developmental: mojum.developmental.local-cell/4
- Learning outcomes: mojum.developmental.local-cell.outcome/4
- Proprioception: mojum.developmental.proprioception/2
- Committed marker: mojum.aquarium.epoch-transition/1
- SQLite checkpoint: mojum.aquarium.checkpoint/2

Every v4 synthetic frame retains one **absolute monotonic tick**.
epoch_index = floor(absolute_tick / 9999);
epoch_tick = absolute_tick modulo 9999. The browser verifies the
observation, intention, measured-learning and frame tick all agree.
The UI keeps the same minimal single-screen layout and shows
"9999 · эпоха 1:0" in the existing tick field at the boundary.

For this deliberately *read-only* bounded JSONL segment format, the
footer must explicitly pin:
1. Original checkpoint/2 policy_schema and the total absolute tick.
2. Bounded trace_range and exact local WebCrypto frame checksum.
3. An epoch_boundaries array with each intervening persisted marker,
   limited to the visible segment. Only {schema, absolute_tick,
   epoch_index} metadata is included; not the private whole journal.

The browser rejects a missing/wrong epoch marker, wrong observation
absolute/epoch tick, intent or measured outcome epoch, old sense schema,
old checkpoint/1 masquerading as v4, and unsupported future policy IDs.
Legacy v1/v2A/v2B/v3B checkpoint/1 and historical exports retain their
prior behavior and do not acquire artificial epoch information.

## Verification against the actual other developer's v4

A new CI workflow checks out exact original PR #132 SHA
51d21f77b887f72cb68960a194e529d6073e3187 into a **separate
directory and Python process**. It executes a real 10,003-frame
v4 Aquarium with actual durable SQLite and one epoch-00000001
marker (10,005 journal events = bootstrap + marker + frames),
then fully cold-reopens and validates the original checkpoint,
event hash chain and source head under its own original runtime.

Five real persisted frames, absolute ticks 9998..10002, are
selected only after verifying their original row numbers and epoch
marker. An **explicit QA-only serializer** wraps those PRIVATE
synthetic frames with the digest/footer metadata. The original
#132 branch is NOT claimed to implement this exporter.

Real Google Chrome opens the same 5-frame segment on desktop
(1280 CSS px) and emulated phone (390 CSS px), verifies the
absolute tick and epoch display 9998 -> 9999, and checks browser
WebCrypto. An additional 5 deliberately tampered files recompute
frame SHA-256 but try to remove marker, falsify epoch index,
wrap intent epoch, downgrade checkpoint or downgrade sense:
every negative case must fail closed before playback.

The UI can inspect a segment, not the whole 10k history in a
single browser import. Existing MAX_FRAMES=5000 and 24 MiB
budgets remain. Exporting actual private history is opt-in and
must retain its original hash-chain/ACL/visibility checks.
The record-embedded SHA alone is **not** origin authentication.

## Limits

A real Linux/Chrome synthetic QA does not prove mobile Android
battery reliability, an infinitely growing journal, neural cognition,
MuJoCo body or cross-device continuity. The peer policy is still
bounded by 99,999 absolute motor ticks, a 100,000 total journal
event budget, and v4 explicitly rejects the older toy_probe
associative sensory model. Never silently wrap these bounds.

No source SQLite database, app main, user-owned journal or
developer branch is modified. The two writable v2A/v2B ABIs
remain different and issue #116's intentionally red gate
must remain RED. Independent code review and explicit user
release authorization required; do not close #116/#129/#133
or merge the stacked Draft PR by automated results alone.
