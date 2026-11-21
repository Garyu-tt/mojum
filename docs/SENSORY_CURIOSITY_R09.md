# Mojum R0.9 — object curiosity before object knowledge

**Experimental, additive CPU-first fixture, isolated under `mojum.sensory_curiosity_lab` to preserve the canonical `mojum.sensory_play` public API.** This branch starts at
`main`, not at the R0.7/R0.8 observation PR chain. Its output is a new
private journal type and does not retrofit or silently migrate existing
organisms. It is intended to become a reusable low-level sensor/memory
adapter in the actual infant aquarium **after independent acceptance**.

## What the organism really receives

The experimenter places two synthetic objects with fixed geometry,
numeric RGB reflectance and a hidden `softness` measurement, used only
as test truth when touched.

The organism **does not** receive the objects' IDs, material names,
softness values, labels or distance coordinates. Its near-field
sensor reports only a boolean visible flag, 3 channel integers, and
distance quantized into ten-centimeter buckets. This is a synthetic
proximity sensor, **not a camera image or a real field of view**.

At the start the association memory is **empty** and there is no
navigation goal. At each virtual tick an innate, bounded motor-babbling
controller chooses from rest, X+/X-, Y+/Y- or a synthetic `probe`
when a near-field cue is present. The object cannot be harmed or
modified: touch is a read-only numerical fixture. Moving stays within
2 or 4 cm per tick. No hardware control channel exists.

When the probe actually contacts an object, the environment produces
one observable scalar `measured_deflection`. The model updates a running
mean for that *RGB cue*, from the **measured result only**. At the next
encounter it can make a numeric prediction. Its error and evidence
count are journaled. There is no pretrained material classifier,
symbolic understanding, reward maximization, artificial emotion or
personality claim.

Example reasoning, described by an *external observer*, not by the
organism as an internal monologue:

> At the first touch with RGB [220,70,30], the expected deflection was
> unknown. The sensor measured a synthetic 0.75 deflection; the
> association count became one and its predicted value became 0.75.
> A later equivalent cue can be compared to that estimate.

This is learning a simple association, **not knowing what a toy is**.
Two objects with identical RGB but different responses currently
produce conflicting evidence in the same associative record. That
failure is informative: later stages should discover spatial/context
invariance and object persistence rather than have labels preloaded.

## Run and check

```bash
PYTHONPATH=src python -m mojum.sensory_curiosity_lab \
  --db ./dante_sensory.sqlite3 --ticks 150 --watch \
  > ./dante_sensory_private.jsonl

# Same organism; private SQLite head preserved, virtual clock resumes.
PYTHONPATH=src python -m mojum.sensory_curiosity_lab \
  --db ./dante_sensory.sqlite3 --ticks 100 --watch \
  >> ./dante_sensory_private.jsonl

PYTHONPATH=src python -m unittest discover -s tests -v
```

The JSONL file includes both sensory frames and a final checkpoint
summary per invocation. Open the standalone local
`examples/sensory_observer.html` page in a browser and choose
`dante_sensory_private.jsonl`. This dedicated, offline viewer shows
the spectator's toy-room scene, what numerical cue the organism received,
whether it tried to probe, the real synthetic contact feedback,
the numeric prediction before/after, its error, and memory evidence count.

The browser uses only an explicitly selected local file; it makes no
network requests or third-party imports. Its validation is for UI hygiene,
not cryptographic authenticity. This is a replay after file creation,
**not** live file-following yet. The existing R0.7/R0.8 motor-only HTML
viewer is a different schema and must not silently mix trace formats.
The viewer also has access to spectator-only scene geometry; that
metadata does **not** mean the organism sees it.

## Source truth and privacy

- `SensoryPlay` appends PRIVATE per-agent events to the local
  SQLite Noosphere log only after a full synthetic step is computed.
  No memory/world/RNG state is adopted in RAM before append success.
- Reopening validates the hash chain, original scene/start/graph/seed
  manifest and exact deterministic replay of *every* committed event.
  This is integrity checking, **not** cryptographic authentication.
- Default Noosphere dataset projections exclude all these PRIVATE
  events. The local explicit `--watch` stream is also private. No
  server, remote feed or third-party transmission is implemented.
- Object IDs and softness appear only in a
  `spectator_scene` record, explicitly marked unavailable to the
  organism. The agent's sensor and its association predictor never
  receive them. A host with direct access to the code and database
  must still be trusted — there is no sandbox boundary against a
  malicious host or operator.
- This lab does not simulate touch mechanics, pressure sensors or
  real-world perception; it demonstrates causal data flow and
  incremental cue learning in a reproducible fixture.

## Next developmental questions

1. **Does the agent distinguish objects across encounters?** Not yet:
   matching RGB values collide in the same table. Test feature
   conjunctions and observations over time, not pre-assigned names.
2. **Does it explore more than random movement?** Not yet: action
   choice is deterministic motor babbling, not a learned curiosity
   policy. Compare information-gain and naive novelty policies only
   after evidence and safety gates are reliable.
3. **Can it learn from others?** Not in this stage: no social graph,
   imported biography, private third-party memories, or shared
   preference judgments.
4. **Can a spectator view what it looked at?** Only the synthetic
   numeric cue; no camera feed or neural attention mechanism exists.

Acceptance is an honest, inspectable chain
`cue → action → measurement → prediction update → retained memory`,
not a requirement to exhibit mature human-like behavior immediately.
