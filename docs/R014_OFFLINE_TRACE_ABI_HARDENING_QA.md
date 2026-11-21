# R0.14 offline trace ABI hardening (review-only)

The existing one-file offline Aquarium viewer displays only synthetic
replay-verified PRIVATE JSONL on the local device. It must not silently
present unknown or competing motor policy schemas as if they were the
recognized local_cell_v2 algorithm.

## Behavior and trust boundary

1. The explicit recognized developmental schemas are legacy coarse v1
   (mojum.developmental.explorer/1) and the opt-in #112 v2A
   (mojum.developmental.explorer.local-cell/2). A frame with no
   developmental component remains a scripted demonstration.
2. A developmental frame requires an object-valued learning record with
   exactly matching schema. Unknown policy schema, missing learning
   schema, or a mixed run of v1/v2 frames fails before adopting any
   UI playback frames; the controls stay disabled.
3. The replay-verified SQLite-to-PRIVATE JSONL exporter now adds a single
   harmless footer field policy_schema, pinned from the committed
   checkpoint's developmental snapshot schema (or 'scripted' for no
   developmental policy). It exports no learned records or internal
   neural state. Already exported history bytes are not rewritten.
4. A viewer reading a footer with policy_schema must match it to every
   frame's policy schema. Old JSONL exports without policy_schema retain
   their earlier frame-based behavior; no silent format migration.
5. A test deliberately changes only the footer schema with unmodified
   SHA-256-valid frame bytes and expects rejection in real Chrome.
   Another test changes all frames to the competing #115 v2B schema
   while recomputing the frame SHA and matching the footer; the viewer
   still rejects it as unsupported. Missing learned schema similarly
   fails after recomputing the digest.

This is **wire-format consistency only**, not a cryptographic signature.
A malicious party who controls both the JSONL frames and footer can
construct other misleading values even if the viewer enforces this
known-schema subset. The SHA-256 checksum guards accidental corruption
but does not establish event provenance; independent source trust and
the SQLite hash/owner audit still matter.

## Branch compatibility and unresolved release decisions

PR #112's v2A implementation and peer PR #115's v2B implementation
both advertise the public name local_cell_v2, but persist different
bootstrap, intent, learned-outcome, checkpoint, and snapshot schemas.
The real cross-SQLite incompatibility is proven by PR #123, issue #116,
and inspected by #126. This viewer guard deliberately does **not**
choose a canonical R0.14 implementation or translate v2B data into
v2A format. Supporting v2B or v3 requires an explicit versioned
registry and independently tested import semantics.

## Reproduction

Run on CPython 3.11–3.13, CPU/stdlib:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_trace_policy_wire_metadata.py -v

The real headless Chrome smoke performs four positive combinations
(coarse_v1/local_cell_v2a × desktop 1280/mobile emulation 390 CSS px)
and explicit mixed-schema/unknown-schema/changed-footer/missing-learning
negative controls, all from disposable synthetic SQLite fixtures.

No main/parent/source PR mutation, no physical movement, no device
deployment, no migrated private journals or general agent intelligence.
Independent code/privacy review and owner release approval remain
required before any integration or merge.
