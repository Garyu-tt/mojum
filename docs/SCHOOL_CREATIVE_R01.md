# Mojum creative school — curriculum-only R0.1

**Status:** Proposed machine-readable catalog only. The released `main` branch does not yet execute lessons, assess creative work, or host a school UI. This change does not claim trained skills, emergent creativity or subjective experience.

## Scope

The initial catalog lives at [`examples/school_catalog_r01.json`](../examples/school_catalog_r01.json), schema `mojum.school-catalog/1`. It includes the previously proposed **music** school together with **visual arts, 3D/sculpture, writing, theatre, dance, photo/video, animation, design, crafts, and architecture**.

Every section has a stable ID, a title, skills to practice, possible artifact types, links to other subjects, and one optional starter activity with a concrete observation target. `implementation_status: PLANNED` means a lesson is **not** wired into a simulation. Neither these prompts nor generated artifacts are evidence that Dante learned anything.

## Intended learning cycle

`observe → experiment → make → revise → reflect`

1. A learner elects to explore an activity or receives a non-coercive invitation.
2. An authorized world/tool adapter presents a bounded scene or workspace. A CPU-only equivalent must remain possible; GPU, camera, microphone, robot and cloud calls must be optional.
3. The learner attempts something and may revise it; the environment produces observable feedback, not assertions about the learner's feelings.
4. The activity can output a versioned artifact (drawing, rhythm, script, motion clip, 3D mesh, etc.) and optional reflection.
5. Later trials can assess retention, generalization and **cross-subject transfer**, with independent baseline and comparison tasks. More modules alone do not demonstrate intelligence gains.

## Future event contract (proposal, not implemented)

An activity runner may later write `mojum.school-event/1` records with a stable anonymized learner reference, logical `sim_tick`, section/activity IDs, authored action, observation, artifact content hash, tool/runtime version, provenance, and **PRIVATE by default** visibility. Event capture must respect the Noosphere permission boundary; a public observation dashboard must use explicit redaction/authorization, never raw private diaries.

Assessment should emphasize progress, curiosity, revision, and demonstrable skill transfer rather than fixed labels of ability or coerced grades. Exercises can be replayed but should avoid assuming that a deterministic sample implies genuine learning.

## Integration steps

- **R0.1 (this change):** Static catalog and structural checks. No new dependencies or engine behavior.
- **R0.2:** A sandboxed CPU-first task runner for one art exercise and one music exercise, producing artifacts and observable feedback.
- **R0.3:** Optional scene/graphics/audio/body adapters with bounded budgets; no unreviewed physical actuation.
- **R0.4:** Noosphere learning event projection, private progress timeline, voluntary course selection, and comparison-based transfer evaluation.
- **R0.5:** Interactive UI showing what was attempted, produced, corrected, and retained, without claiming consciousness or feelings.

The school should share the existing GENESIS development timeline and simulated-world interfaces instead of creating eleven separate training engines. Absent food/water, failures, grades, and age must not become hidden survival or automatic shutdown conditions.

## QA

`PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_catalog.py' -v`

Structural tests validate ID uniqueness, cross-section references and required observations. They do **not** test actual lessons or creativity.
