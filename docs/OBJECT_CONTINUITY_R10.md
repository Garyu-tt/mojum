# Mojum R0.10 — provisional object continuity from lived numeric experience

**Experimental CPU-only, private and opt-in.** R0.10 is stacked on
[PR #33](https://github.com/Garyu-tt/mojum/pull/33), not merged into
`main`. Its purpose is to test whether an untrained toy-room inhabitant
can retain *uncertain* hypotheses about repeat sensory encounters without
being given object identifiers.

## What changes over R0.9

R0.9 associates one three-channel RGB cue with measured probe deflection.
Two distinct toys with identical RGB values end up sharing one prediction:
a known form of perceptual aliasing, not learned identity.

R0.10 adds a second numeric *egocentric* sensor in an opt-in mode:
- existing near-field RGB and distance bucket (10 cm granularity);
- eight-sector egocentric azimuth, e.g. east/northwest (45-degree bins);
- the organism's own current numeric position (proprioception);
- private retained encounter history, empty initially.

The hypothesis tracker estimates a rough observed landmark point
using the quantized range, sector midpoint and self-position. For each
numeric cue, it tentatively links to a track when the estimate falls
within **0.32 m** of exactly one previously seen track. Otherwise:

- **No match:** create an untrained, local hypothesis `h0001`, `h0002` …
- **One match on consecutive ticks:** `continuous_candidate`.
- **One match after an absence:** `possible_reencounter`.
- **Several matching tracks:** `ambiguous_identity`. Do not merge
  identities or assign touch measurements to an arbitrary candidate.
- **No visible cue:** `no_visible_signal`.

A track contains a rounded location estimate, number of encounters,
last observed tick, measured contact count and empirical running mean
of *contact response*. These data come only from numeric sensory
evidence and previous local experience, **not** the experimenter's
toy ID, world-space geometry, material label or hidden softness.

**Critical limitation:** these are *provisional local track labels*, not
proof of persistent physical object identity, actual object recognition,
triangulated SLAM, vision or cognitive understanding. Different items
close together can alias; noisy position and range can fragment a real
object into several hypotheses. The tests explicitly check ambiguity.

## Controlled comparison with the R0.9 baseline

In an explicit **two-sighting fixture** (not a statistical or
held-out generalization benchmark), two distant toys share the same
RGB cue but produce measured contact deflections **0.1** and **0.9**.

- **R0.9 RGB-only association:** after both observations, one shared
  mean prediction of **0.5**, losing the distinction.
- **R0.10 provisional spatial tracks:** two separate local hypotheses
  retain contact estimates **0.1** and **0.9**.
- **R0.10 ambiguity control:** when a later observation falls within
  range of two hypotheses, **no identity assignment** and **no contact
  profile update** is performed.

This demonstrates only that a simple local cue+location hypothesis
avoids one constructed RGB-aliasing failure. It does not establish
reliable object recognition across moving scenes, occlusions, noise
or real cameras.

## Run from a checkout of this branch

```bash
PYTHONPATH=src python -m mojum.sensory_play \
  --db ./dante_r10_private.sqlite3 \
  --object-tracking --ticks 180 --watch \
  > ./dante_r10_private.jsonl
```

Open `examples/sensory_observer.html` locally and select the JSONL
file. The observer shows a new **Память о предполагаемом объекте**
panel alongside the numeric sensation, real synthetic probe result,
evidence counts and running prediction. The viewer also accepts
old R0.9 logs without that optional panel's data.

To continue *the same organism's* experience:

```bash
PYTHONPATH=src python -m mojum.sensory_play \
  --db ./dante_r10_private.sqlite3 \
  --object-tracking --ticks 180 --watch \
  >> ./dante_r10_private.jsonl
```

Omitting `--object-tracking` on reopening **fails closed** rather than
silently discarding the track model. Likewise, a preexisting legacy
R0.9 journal cannot be reopened as R0.10 without an explicit
migration (none exists). Start a separate private database for new
experiments; old data and code retain their interpretation.

## Storage and safety

All tracks live in each organism's **PRIVATE** SQLite Noosphere event
log, embedded in the deterministic step record and reconstructed on
reopen. Learning and world/RNG updates adopt new RAM state only after
a successful SQLite compare-and-swap append. A conflicting writer
cannot mutate the losing process's model.

The model is CPU-first: bounded to **64 tentative tracks** and
**3,999 frame ticks**. Each frame reconstructs a bounded snapshot
and compares numeric estimates against local hypotheses. There are
no GPU, pretrained semantic weights, network sockets, camera inputs
or hardware actuation channels.

This is only a local experimental privacy boundary: a host operator
with raw file access can inspect and modify the fixture. The log's
hash chain is an integrity check, not cryptographic authentication
without a separately trusted external anchor. The JSONL viewer is
file-only and does not verify the source chain; it must not be
automatically published.

## Reproducible QA acceptance evidence

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Dedicated `tests/test_object_memory_r10.py` checks:

- The model starts without any ID, material label or learned objects.
- Two spatially separated toys with the **same RGB** and different
  contact responses create distinct **tentative hypotheses**, without
  being fed their experimenter-defined names.
- A loss of visual cue followed by reacquisition produces a tentative
  same-object association with an explicit time gap.
- Overlapping same-RGB candidates produce **ambiguous_identity**
  without contaminating either learned contact profile.
- Rejects forged direction sectors, absent/stale ticks, malformed
  snapshots, NaN, unlicensed knowledge, loss of private journal CAS,
  incompatible resume flags and altered bootstrap.
- Original R0.9 journal schema/behavior remains unchanged by default.

## Next developmental step

Test observation continuity under perturbed sensor readings, temporary
occlusions and moving toys, with held-out scenes and honest
false-merge/false-split metrics. Only then consider learned
active exploration or increasing biological realism. Do not attach
personality ratings to fragile object-hypothesis labels.
