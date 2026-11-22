# Spatial, creative and logistics school — proposed curriculum R0.2

**Status:** Curriculum metadata only. This does not implement an autonomous
teacher, real spatial simulation, robotics, physical engineering certification
or demonstrated learning. The canonical catalog is
[`examples/school_catalog_r01.json`](../examples/school_catalog_r01.json),
schema `mojum.school-catalog/1`. Revision `0.2.0-proposal` adds optional
`lessons` lists without changing the meaning of existing starter activities.

## New and expanded subjects

- **Sculpture and 3D:** volumetric primitives, proportions, materials,
  context and accessible placement. Begin with a geometric object; progress
  to a sculpture designed for its surroundings.
- **Architecture and spatial design:** plan and scale, lighting,
  accessibility/ergonomics, modular structures and construction staging.
- **Logistics (core, not elective):** route planning, inventory, scheduling
  and dependencies, disruption recovery, resilient supply chains.
- **Urban planning and mobility:** neighborhoods, walkability, transport
  flows, basic utility/resource networks.
- **Structural engineering foundations:** simplified balance, loads,
  construction sequence and bills of materials. These are educational
  approximations, not evidence that a real structure is safe.
- **Design/drafting and crafts:** projections, prototyping, assembly,
  material budget and waste minimization.

Each lesson has a stable subject-qualified `id`, `level`,
`goal`, explicit simulated `activity`, expected observable `evidence`,
and `transfer_to` subjects. Progression is capability-adaptive rather than
forced by a virtual age. Repeat/revision is allowed, grades and penalties
are not required, and curriculum participation is voluntary.

## Sample cross-disciplinary challenge: build and supply a workshop

1. Sketch a sculpture and compare three viewing angles.
2. Draft a room and specify the workstations, clearances and lighting.
3. Make a simplified structural and material plan; compare at least two
   alternatives under known *simulated* constraints.
4. Record required components in inventory and derive a delivery schedule.
5. Route virtual vehicles and measure total distance, delays, unfilled orders
   and resource conflicts.
6. Block one route or remove one item; re-plan and compare against the baseline.
7. Put the workshop in a neighborhood plan, check access routes and revise.
8. Review outcomes and record tradeoffs, provenance and what was **not**
   measured, rather than assigning a fixed score to the learner.

Use the existing `observe → experiment → make → revise → reflect` loop.
Potential artifacts include drawings, geometry, maps, route plans, inventory
records and operation dependency graphs. Keep learner events PRIVATE by default;
any Noosphere integration needs explicit authorization, provenance, and
redaction. CPU-first deterministic fixtures are preferable for early tests.

## Implementation boundary and QA

The current R0.2 scripted runner in `src/mojum/school.py` supports only
`visual_arts.starter` and `music.starter` as synthetic exercises. This PR
does **not** claim that architecture/sculpture/logistics have executable lesson
runners. A future scoped issue can add an offline simulation for routing,
inventory and spatial constraints with independently measurable baselines.

Check the structural contracts with:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_*curriculum.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_catalog.py' -v
```

The tests cover curriculum shape and compatibility, **not** educational
outcomes, persistent skill acquisition, transport physics or real-world safety.
