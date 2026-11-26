# GENESIS somatic body ↔ Bassy adapter (experimental R0.1)

**Status:** typed, deterministic, CPU-only **fixture**. There is no modeled 3D
anatomy, Bassy automation, glTF renderer, validated human-growth science, sex
model, reproductive system, voice model or physical body in this PR. Do not
describe these contracts as a completed digital organism.

## Separation of responsibilities

1. **GENESIS genotype/ledger** (`src/mojum/genesis.py`): immutable inherited
   base genes and acquired changes. Existing v1 remains unchanged.
2. **Somatic chromosome** (`mojum.genesis.somatic-chromosome/1`): a separate,
   strictly validated inherited chromosome with four fixed-point morphology
   modifiers. It must have the same `genome_id` as the GENESIS phenotype.
   This avoids silently changing v1 seed hashes and breaking existing aquariums.
3. **Developmental age:** 6–16 **integer** years, passed separately from the
   genome. One genotype therefore produces multiple time-dependent body forms.
   Age does not equal learned competence, consciousness or developmental stage
   of the simulated cognitive system.
4. **Somatic compiler:** `compile_body(ledger.phenotype(tick), chromosome, age)`
   emits height and relative head, leg, shoulder ratios. Baseline curves are
   toy values and must be replaced or calibrated before realism claims.
   The existing acquired `body_scale_milli` affects future body projections;
   the inherited somatic chromosome is currently static (no somatic
   recombination, evolution or acquired somatic events).
5. **Agent identity:** `mojum.agent.identity/1` is a separate record containing
   `agent_id`, `display_name`, and `assigned_by: human`. Neither names nor
   naming rules belong in the inherited genetic code. The present validator
   enforces only format/declared origin, **not** verified human authorization;
   assignment events and persistence must be added later.
6. **Appearance:** deliberately undecided. No hard-coded hair, eye, skin,
   stylistic, gender or sexual-characteristic genes. The neutral humanoid
   *fixture* contains no reproductive anatomy. A later explicit appearance
   policy may reference externally authored Bassy presets/GLBs.
7. **Bassy:** external editor and manual GLB export, **not open source** and not
   a runtime dependency. Imported exports are referenced by a strict
   `mojum.avatar.bassy-manifest/1`, not embedded in the repository.

## Read-only aquarium API

```python
import json
from pathlib import Path
from mojum.aquarium import Aquarium

seed = json.loads(Path("examples/genesis_seed.json").read_text())
graph = json.loads(Path("examples/first_loop.json").read_text())
somatic = json.loads(Path("examples/somatic_chromosome.json").read_text())

with Aquarium("dante.sqlite3", seed, graph, neural_mode="disabled") as a:
    child = a.body_preview(somatic, age_years=6)
    adolescent = a.body_preview(somatic, age_years=16)
    assert child["genome_id"] == adolescent["genome_id"]
```

`body_preview` does not write SQLite events, change neural state, or reset
memories. Existing bootstrap/replay hashes and v1 genome seed remain intact.

## Bassy export workflow and limits

- Author one recognizable identity in Bassy. Export local **GLB** age
  keyframes (recommended 6/9/12/16); do not assume the editor exposes a public
  automation API or supports direct parameter-to-genome conversion.
- Measure each exported model's intended native height, note its rig ID,
  compute SHA-256 for the GLB. Store **relative** paths underneath an asset
  root, never absolute filesystem paths or URLs.
- Validate the manifest with `validate_bassy_manifest`, select the nearest
  keyframe using `select_bassy_asset`; the result supplies a fixed-point
  scale multiplier. Monthly previews use the exact developmental `age_months`
  (72..192), which must agree with the whole-year `age_years` floor. For example,
  127 months selects the 12-year keyframe from 6/9/12/16, while the exact
  midpoint at 126 months retains the younger 9-year keyframe. Whole-year
  previews retain their existing selections and result fields; the result's
  `age_years` remains the body's whole-year age. This is **keyframe selection,
  not mesh interpolation**.
  Rig compatibility across different exports is **not guaranteed**.
- Before loading, call `verify_local_glb(root, selection)`. It checks a
  root-contained resolved path, size, binary GLB2 header and SHA-256. It is
  **not a full glTF parser** or a file sandbox. A real renderer must use a
  hardened glTF parser and resource limits.
- Any client rendering a Bassy-derived character must visibly include
  **Made with Bassy** and the editor URL; a return flag is not an implemented
  viewer. Verify current Bassy distribution terms before publishing assets.
- No Bassy executable, proprietary templates, human/child meshes, textures
  or third-party models are committed here.

## Example manifests

A valid *shape* (the hashes must be replaced with hashes of real GLBs):

```json
{
  "schema": "mojum.avatar.bassy-manifest/1",
  "genome_id": "dante-fixture-001",
  "generator": "Bassy",
  "credits": {
    "text": "Made with Bassy",
    "url": "https://bassycharactercreator.itch.io/bassy-character-creator"
  },
  "stages": [
    {
      "age_years": 6,
      "path": "dante/age_06.glb",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "native_height_mm": 1150,
      "rig_id": "measure-after-export"
    },
    {
      "age_years": 16,
      "path": "dante/age_16.glb",
      "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "native_height_mm": 1690,
      "rig_id": "measure-after-export"
    }
  ]
}
```

Those are **documentation placeholders**, not existing downloadable assets.

## Remaining implementation gates

- Identity assignment as an authenticated append-only Noosphere event
  (preserve creator provenance), not just a human-declared JSON field.
- Real somatic phenotype → Blender/Bassy manual-export calibration, rig mapping,
  LOD and animation retargeting. Programmatic Bassy support only if real APIs
  or legally permitted export automation are verified.
- Explicit avatar ownership/consent/redistribution records; asset hash checks
  before use; renderer attribution, mesh validation and low-resource fallback.
- Age progress controlled by a developmental clock, not by GUI slider alone.
- Physical embodiment/MuJoCo, growth dynamics and learning are separate later
  milestones. Changing the avatar must never erase agent history.


## Persisted developmental clock and naming (experimental R0.2)

The aquarium now has an **explicit, journaled developmental clock** in whole
months, initially 72 (6 years) and bounded by 192 (16 years). This clock is
separate from GENESIS chromosomes, simulation physics ticks, real wall time,
brain maturity, learning progress and user-facing display names.

```python
from mojum.aquarium import Aquarium

with Aquarium("dante.sqlite3", seed, graph, neural_mode="disabled") as a:
    age_6 = a.body_preview(somatic)  # no override: persisted 72 months
    a.advance_development(6, reason="controlled virtual semester")
    age_6_and_half = a.body_preview(somatic)  # interpolated 78 months
    a.assign_human_name(
        {"schema": "mojum.agent.identity/1",
         "agent_id": seed["genome_id"],
         "display_name": "Данте",
         "assigned_by": "human"},
        actor_id="local-owner",  # unverified in this offline fixture
    )
```

- `body_preview(somatic, age_years=6)` still supports a read-only what-if
  preview. Without an override, `body_preview(somatic)` uses the committed
  developmental clock and interpolates shape dimensions between yearly samples.
  **No GLB mesh morphing or rigged animation is implemented.**
- `advance_development(months=1..12, reason=...)` appends a
  `development.advance` event to the existing PRIVATE Noosphere SQLite log,
  without rewriting genome, frame events, learner, room or identity. Multiple
  advances can reach age 16; attempts beyond the bound fail closed.
- `assign_human_name(identity, actor_id=...)` appends one
  `identity.assigned` event. Both declarations and the actor identifier are
  caller-supplied and **are not authenticated**; they must be guarded by an
  authenticated human-facing service before real deployment. Rename and name
  generation are intentionally unsupported.
- Reopening the same aquarium replays naming and age milestones alongside
  frames. Old fixture logs containing only bootstrap, frames and acquisitions
  remain readable. The bootstrapping manifest and genotype v1 hash are
  unchanged.
- No automatic time progression is assumed: an external, controlled
  developmental scheduler must explicitly advance the clock. This is a
  chronological *simulation* mechanism, not a calibrated child growth
  prediction or evidence of mental/physical maturity.
- No visual-appearance genes, sex/reproductive anatomy, voice maturation or
  biological puberty model are introduced.

Run new and existing regressions with:
`PYTHONPATH=src python -m unittest discover -s tests -v`.
