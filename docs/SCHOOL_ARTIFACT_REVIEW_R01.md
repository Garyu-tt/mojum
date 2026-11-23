# School artifact review R0.1

**Status:** opt-in, offline, read-only structural inspection of the current school fixture runner's PRIVATE visual-art and rhythm artifacts.

## Purpose

The existing creative-school runner emits two artifacts per run: a make artifact and a revise artifact. This module checks that these artifacts still match the supported wire subsets, verifies their digests, and compares a few deterministic structural measurements.

It is deliberately **not** a teacher, aesthetic evaluator, musical critic, or learning model. A changed artifact is not necessarily a better artifact. No skill, talent, competence, mastery, creativity, progress or transfer score is inferred.

## API

    from mojum.school_activity import run_activity
    from mojum.school_artifact_review import review_run

    run = run_activity(catalog, "visual_arts.starter", script)
    report = review_run(run)

The same API accepts music.starter runs. The supplied run must be a valid PRIVATE moj​um.school-run/1 object; review_run delegates the existing run/event/hash checks to school_activity._check_private_fixture_run before deriving measurements.

The result is also marked PRIVATE. It includes artifact phase, media type, SHA-256 digest and structural measurements only. It intentionally omits learner reference, event observations and raw artifact content. Callers remain responsible for consent and access control. Neither the run's local SHA values nor this report authenticate who created it.

## Checks and measurements

### Visual art

Only the current fixture SVG subset is accepted: a fixed 64×64 SVG canvas, fixed white background, one stroke group, and 1–32 path elements using one M x y L x y line segment each. DTD/entity declarations, scripts, unknown elements/attributes, extra SVG commands, out-of-range coordinates and oversized content are rejected.

The report counts strokes and sums Manhattan segment length in grid units. This is a structural descriptor, not a visual similarity or composition score.

### Rhythm

Only the moj​um.rhythm-grid/1 JSON shape is accepted: grid_steps=64 and 1–32 unique, sorted integer pulse positions in [0, 63]. Duplicate JSON keys, non-finite values, malformed UTF-8, wrong types, unsorted/duplicate pulses and out-of-range positions are rejected.

The report includes pulse count and cyclic gaps between pulses, including the wrap from the final pulse to the first pulse in the next 64-step cycle.

### Revision comparison

For SVG: added/removed stroke counts and the change in Manhattan length.

For rhythm: added/removed pulse counts, symmetric difference and pulse-count delta.

The report separately says whether the two artifact digests differ. These measurements do not establish intent, quality, correctness, learning or improvement.

## Bounds and isolation

- Standard library only; no mandatory dependency additions.
- No filesystem, network, audio, renderer, device, SQLite or Noosphere access.
- Existing school runner and its journal contract are not modified.
- Each artifact is bounded by the runner's 16 KiB artifact limit.
- SVG is parsed as XML but only the narrow generated structure is admitted; this module never renders or executes SVG.
- Rhythm JSON rejects duplicate keys and non-finite constants.
- Reports are deterministic and contain no raw learner identifier or artifact content.

## Reproduction

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_artifact_review.py' -v

This module evaluates only the current synthetic fixture artifacts. It does not establish external provenance, authenticated consent, real-world artistic ability, music performance, an autonomous curriculum or a trained learner.
