# R0.15: Local Cell v4 epoch capacity witness (offline, advisory)

A read-only, deterministic utility for the **existing** bounded v4 Aquarium event wire. It cannot authenticate, repair, append, compact, or migrate a PRIVATE journal. It never opens SQLite and has no network or device access. Older v1/v2/v3 journal/policy contracts are not supported, reinterpreted, or changed.

## Inputs and invariants

`absolute_tick` is the **next uncommitted frame tick**, `epoch_index` is the last committed/adopted epoch marker, `stored_events` is the trusted caller's independently obtained event count and `steps` is the requested number of future frames. Input is not evidence; an adversary can supply fabricated but internally consistent counts. Independent journal replay/HMAC verification and CAS remain mandatory.

The current v4 protocol has exactly **one bootstrap**, one event per committed motor frame, and one marker before the first frame of each additional epoch. A canonical cursor therefore has `stored_events == 1 + absolute_tick + epoch_index`. Between epoch boundaries, `epoch_index = absolute_tick // 9999`. At an exact nonterminal boundary the marker can still be pending (`epoch_index - 1`) or already durable (`epoch_index`); both states produce the same future total without double-counting. At terminal `absolute_tick=99990`, marker 10 is not permitted.

The existing source contract has a hard cap of **99990 frames and 100000 events**, including 9 epoch markers and the bootstrap. The witness refuses unsupported indices, boolean/floating inputs, mismatched counts and plans exceeding the pinned frame/event budgets. This is **not** indefinite runtime, authenticated history, rollback protection or segmentation beyond 100k.

## Run

From an installed Mojum package or with `PYTHONPATH=src`:

```sh
python -m mojum.epoch_capacity_witness --absolute-tick 9999 --epoch-index 0 --stored-events 10000 --steps 1
```

Expected: one `9999` marker and two additional events (marker, frame). For a crash/restart after committing the marker but before the next frame, run with `--epoch-index 1 --stored-events 10001` and the same absolute tick; zero additional markers and one frame are expected. Both projections end with 10002 events.

The JSON schema `mojum.aquarium.epoch-capacity-witness/1` is a **derived advisory statement**. Never use it as an authorization token, accept a journal on its own, store PRIVATE event payloads in it, or skip actual SQLite CAS and replay checks. The optional standalone command is stateless and emits metadata only.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_epoch_capacity_witness.py -v
```

Unit cases exercise the 9998→9999 boundary, the marker-written/next-frame-unwritten failure window, full 99990-frame/100000-event admission, forged phase/count/type rejection and CLI no-partial-output error. Other subsystems, a genuine SQLite append/cold replay, cryptographic verification, actual hardware and continual operation remain separate acceptance gates under #129.
