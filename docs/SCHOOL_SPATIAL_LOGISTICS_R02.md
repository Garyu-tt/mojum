# Spatial, creative and logistics school — proposed curriculum R0.2

**Status:** Curriculum metadata only. This does not implement an autonomous
teacher, real spatial simulation, robotics, physical engineering certification
or demonstrated learning. The canonical catalog is
[`examples/school_catalog_r01.json`](../examples/school_catalog_r01.json),
schema `mojum.school-catalog/1`. Revision `0.3.0-proposal` adds optional
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
- **Game-based learning:** optional creative sandbox, factory, logic, physics
  and city-building games as cross-subject laboratories, with an original
  offline alternative for each game case.

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

## Games as a voluntary learning format

Playing is a valid leisure activity in its own right. Do **not** require every
game session to produce a lesson, a grade or a performance score. The learner
may freely choose to play, pause, stop, explore, or decline a suggested lesson.
Rewards must not pressure repeated play; avoid manipulative retention mechanics,
punishments, simulated pain, or mandatory age-gated progression.

The catalog adds `game_based_learning` with six lesson outlines:
- Short original puzzle: observe a rule, try hypotheses, compare approaches.
- Creative construction: Minecraft is one **optional** example; an original
  offline sandbox must remain available for building rooms and structures.
- Factory and logistics: **Factorio** is a reference example for
  resource-to-product flows, belts, inserters, buffers, power, trains,
  delivery networks and bottleneck diagnosis.
- Physics/engineering: Kerbal Space Program or a bounded original
  mechanics fixture to compare predictions and simulated outcomes.
- City design: Cities: Skylines or a small independent urban simulator
  for routes, accessibility and resources.
- Transfer check: try a genuinely different environment and an independent
  baseline/control task before concluding that skills transferred.

### Factory lab: optional Factorio case

The `logistics.factory_06` lesson complements
`game_based_learning.factory_03`. Both are catalog metadata, not software
integrations. In a *no-combat, sandbox or equivalent safe training environment*:

1. Identify an input supply, a processing step and a target manufactured item.
2. Draw the dependency graph and predict items per simulated minute.
3. Assemble the line and record source output, belt capacity, machine
   utilization, intermediate buffer sizes and unfulfilled demand.
4. Compare an alternative with more equipment against an alternative with
   better route/buffer arrangement. Find the bottleneck empirically.
5. Introduce a controlled supply interruption and record recovery time.
6. Build a different layout or use an original standalone simulator to
   test transfer beyond memorizing one game's mechanics.

**Access boundary:** commercial games are optional examples, not bundled
assets or required dependencies. Do not copy proprietary game code, art,
levels or telemetry; using a game requires legitimate access and respecting
its terms. No account, paid purchase, DRM circumvention, GPU, game client
or online connection may be a dependency for Mojum's CPU-first school.
No game control, screen observation, agent integration or record ingestion
is implemented in this curriculum PR.

**Evidence boundaries:** a better factory layout is only evidence of success
in that scenario. Claims about retained general skills require independent
repeat, baseline, transfer task and documented limitations. Learning and
leisure must remain distinct opt-in contexts. Game records and reflections
stay PRIVATE by default, with explicit authorization for sharing.

## Implementation boundary and QA

The current R0.2 scripted runner in `src/mojum/school.py` supports only
`visual_arts.starter` and `music.starter` as synthetic exercises. This PR
does **not** claim that architecture/sculpture/logistics/game-based-learning have executable lesson
runners. A future scoped issue can add an offline simulation for routing,
inventory and spatial constraints with independently measurable baselines.

Check the structural contracts with:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_*curriculum.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_catalog.py' -v
```

The tests cover curriculum shape and compatibility, **not** educational
outcomes, persistent skill acquisition, transport physics or real-world safety.
