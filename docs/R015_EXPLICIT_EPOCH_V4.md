# R0.15 — explicit versioned epoch continuation for Local Cell v4

Status: **experimental CPU-only PR**, stacked on reviewed integration
[PR #125](https://github.com/Garyu-tt/mojum/pull/125) at pinned
SHA `463caec4b1d17bf978622814c377ffcf007e326c`.
No existing v1/v2/v3 PRIVATE journal is modified, interpreted under a
new schema, automatically migrated or deleted.

## Why v4

The canonical old typed `Proprioception/1` rejects tick >=9999.
Simply applying `tick %= 9999` would falsify causal time, record
identifiers, learning update counts, SQLite replay and HMAC state
continuity. Signed snapshots only accelerate replay; they do not
magically remove logical tick restrictions.

The new opt-in `developmental_policy=local_cell_v4` uses:
- `mojum.developmental.proprioception/2`, with monotonic
  `absolute_tick`, `epoch_index`, `epoch_tick` fields.
- `mojum.developmental.local-cell/4` and measured
  `mojum.developmental.local-cell.outcome/4`, retaining the deterministic
  <=512-cell LRU policy and explicit forgotten-evidence counts from v3.
- New PRIVATE journal bootstrap fields
  `epoch_transition_schema=mojum.aquarium.epoch-transition/1`,
  `epoch_ticks=9999`, `proprioception_schema`. A distinct source DB
  must be created for v4; v1/v2/v3 bootstrap/frames stay unchanged.
- Every boundary has an explicit `aquarium.epoch` journal event at
  absolute tick `k * 9999` (k>=1), before the first new-epoch
  `aquarium.frame` event. The epoch marker is hashed and committed
  with the original Noosphere CAS chain, with equal monotonic
  timestamp to the subsequent frame at that same absolute tick.
  `frame-<absolute_tick>` IDs never reset.

Example: tick 9998 is epoch 0 / local 9998. The **next**
movement happens at absolute tick 9999, epoch 1 / local 0.
The event preceding that movement is `epoch-00000001` and
has `absolute_tick=9999`. GENESIS, pulse, shadow learner, actual
RoomState and policy learned evidence are never recreated or wiped.

If a process stops *after* the epoch event commits but *before*
the next frame, SQLite safely stores a pending boundary: the
checkpoint shows unchanged absolute tick plus the newly adopted
epoch index. Reopen verifies the event and continues exactly
one first-frame write; no duplicated marker. A stale writer
cannot claim a checkpoint from a now-changed event head.

The HMAC cached replay state uses a **new explicit**
`mojum.aquarium.replay-state/2` for v4 and pins `epoch_index`.
The original signed `mojum.aquarium.replay-state/1` contract for v1/v2/v3 is unchanged.
A validly authenticated snapshot with the wrong replay-state version is rejected.
During v4 restore the signed epoch is checked against the absolute
tick, the recorded boundary event where applicable, the policy
snapshot and the checkpoint. The **full** source hash chain is
verified on each reopen; signed snapshots are PRIVATE plaintext
and are not encryption or prevention against total source rollback.

## Scope and intentionally unaddressed blockers

- Hard **99,999 total committed motor ticks maximum** in the new
  `epoch_sense_v2`, plus independently existing **100,000 total SQLite
  journal events maximum**. Each epoch transition consumes an
  *additional* event, so the actual event cap is reached earlier than
  the hypothetical 99,999 movement limit. This is a bounded test of
  safe long epochs, **not an infinite organism or circular journal**.
- `toy_probe` is **explicitly refused for v4**. Its legacy
  `ContactAssociations` model has a separate ~4k measurement
  cap and has no compatible versioned long-run retention strategy.
  v4 is tested with `sensory_mode=none`, while heartbeat
  interoception remains active. Integrating a new versioned
  toy-contact retention model is separate future work.
- An exact 9999→10000 crossing is tested on real disposable SQLite
  with synthetic point-room, HMAC snapshot and full cold replay.
  The brain remains `UNIMPLEMENTED`; this is not MuJoCo,
  subjective experience, independent navigation success,
  Android battery/background reliability or secure multi-node sync.

## Reproduce on a NEW local synthetic journal

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db /tmp/fresh-v4-synthetic.sqlite3 \
  --exploration-mode goal_free --sensory-mode none \
  --developmental-policy local_cell_v4 --ticks 120

PYTHONPATH=src python -m unittest discover -s tests \
  -p test_epoch_v4.py -v
```

The foreground portable host also supports
`--developmental-policy local_cell_v4 --sensory-mode none`.
It preflights the **whole requested session** against both
absolute tick and available event budget including extra pending
epoch records, rather than running a partially accepted batch.
Each portable host invocation remains bounded to 1..1000 ticks.

Historical old journals are strictly version-pinned and continue
to replay using their old policy, sense and checkpoint contracts.

## Release gates

No merge to main or actual deployment. Require exact final-head
Python 3.11/3.12/3.13 CI, independently reviewed journal crash
atomicity and evidence persistence, prospective second epoch at
19998, a separate versioned toy-contact-retention design, a
reviewed >100k authenticated segment protocol (not silent
truncation), portable Android smoke, and review/reconciliation
of all stacked ancestor PRs. See [Issue #129](https://github.com/Garyu-tt/mojum/issues/129).
