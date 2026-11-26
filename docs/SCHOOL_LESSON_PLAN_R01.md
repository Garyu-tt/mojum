# School lesson itinerary R0.1 (opt-in, synthetic only)

Related: [issue #885](https://github.com/Garyu-tt/mojum/issues/885), follow-up to completed school #166.

## Contract

`mojum.school_lesson_plan.plan_voluntary_lessons(catalog, section_ids, *,
consent, start_tick=0, phase_ticks=1, require_cross_subject_links=False)`
is a pure, CPU-first **proposal builder**, not an executor or teacher. It imports
only Python stdlib plus the existing `school_activity.validate_catalog`.
The existing `examples/school_catalog_r01.json` is `CATALOG_ONLY_NOT_EXECUTABLE`;
the runner remains unchanged, including its smaller episode-tick boundaries.

It requires a **literal `True`** opt-in every time the plan is built.
No consent, no plan. This only represents the explicit input supplied to the
builder; it is **not** durable consent, and does not authorize execution.
Consent may be withdrawn. A future executor must obtain *fresh* permission,
check capability and runtime limits, and be safe to stop between phases.

The input is a validated `mojum.school-catalog/1`, plus 1–16 unique known
section IDs in the caller's order. Each proposed starter activity has the
five fixed phases `observe / experiment / make / revise / reflect`; each
phase occupies 1–256 virtual ticks. The builder checks that the last tick
does not exceed `2**63 - 1` **before allocating a plan**. No wall clock,
filesystem, network, microphone, GUI, real device commands, or simulator
side effects are used.

With `require_cross_subject_links=True`, each consecutive pair must be
an explicit **directed** `cross_subject_ids` edge from the previous
section to the next. An absent edge is rejected; no subject is silently
appended, and no lesson is forced based on a grade or age.

## Example

```python
import json
from pathlib import Path
from mojum.school_lesson_plan import plan_voluntary_lessons

catalog = json.loads(Path("examples/school_catalog_r01.json").read_text())
proposal = plan_voluntary_lessons(
    catalog, ["music", "visual_arts"], consent=True,
    start_tick=100, phase_ticks=2,
    require_cross_subject_links=True,
)
assert proposal["lessons"][0]["start_tick"] == 100
assert proposal["lessons"][-1]["end_tick"] == 119
assert proposal["executed"] is False
```

The result schema is `mojum.school-lesson-plan/1`. It contains only
section/activity IDs, phase names and virtual tick bounds, the literal
opt-in value, non-execution/non-actuation flags, synthetic provenance,
the cross-subject policy flag, and SHA-256 hashes. It deliberately does
**not** export the catalog's prompts, observation texts, personal details,
grades, or learning outcomes. The `catalog_sha256` is the digest of the
validated canonical UTF-8 JSON input (no trailing newline). The
`digest_sha256` is the digest of the canonical UTF-8 JSON plan without
that digest field. Both are comparison checksums, **not signatures**,
consent proofs or audit authority; the catalog digest can reveal equality
between catalogs and should not be published as a private-record hash.

No actual learning, synthetic sensation, active scheduling, automatic
lesson launch, background jobs or cross-subject transfer is claimed.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_lesson_plan.py' -v
```

Tests cover stable replay/digest, multi-subject five-phase intervals,
directed links, opt-in, unknown and duplicate IDs, malformed catalog,
strict type boundaries (including booleans), capacity and tick overflow,
caller/output mutation separation, and absence of filesystem I/O.

This is an additive, opt-in slice: existing school APIs, aquarium execution,
SQLite/PRIVATE Noosphere state, and curriculum metadata are untouched.
Independent QA, full CI, and exact-head approval are required before merging.
