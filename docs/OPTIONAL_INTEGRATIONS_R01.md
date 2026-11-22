# Optional external integration catalog (R0.1)

Tracking issue coverage: **#689–#702**. This is a proposed dependency/acceptance registry, not a package installer or proof of integration. The Python 3.11+ baseline in `main` remains stdlib-only, and none of the candidates is installed, imported or started by this PR.

- Machine-readable candidate groups: `src/mojum/data/optional_integrations.json`.
- Read-only registry integrity guard: `python tools/optional_integrations.py` (or `--json`).
- Run tests: `PYTHONPATH=src python -m unittest tests.test_optional_integrations -v`.

## Adoption gates

1. Start with separate opt-in adapter PRs: #690 point-room environment, #689 MuJoCo envelope, #691 neural proposal wire.
2. Review precise upstream LICENSE files, package transitive dependencies and model/asset licenses against Mojum's undecided repository license, *before* upgrading status from `planned`. Registry `license_review=required` is not clearance.
3. Pin backend versions, numerical tolerances, CPU/RAM benchmarks, test fixtures and data provenance per issue. Do not silently swap canonical graph/Noosphere formats.
4. Never load private organism histories, credentials or hardware controls into remote providers. No production or real actuator access in this registry.
5. Independent QA compares exact PR HEAD/BASE and release CI before merging; a status change to `accepted` is a reviewed claim, not a side effect of updating this JSON.

## Dependency matrix

| Issue | Optional candidates | Initial status |
| --- | --- | --- |
| #689 physics (P0) | mujoco, mujoco_menagerie | planned |
| #690 gym (P0) | gymnasium, stable_baselines3, dm_control | planned |
| #691 brain (P0) | annarchy, brian2, nengo, snntorch | planned |
| #692 memory_index (P0) | sqlite_vec | planned |
| #693 backup (P0) | litestream, borgbackup | planned |
| #694 speech (P0) | sherpa_onnx, whisper_cpp | planned |
| #695 avatar (P0) | three_vrm | planned |
| #696 fleet (P0) | llama_cpp, litellm | planned |
| #697 eval (P1) | langfuse | planned |
| #698 multi (P1) | nats_jetstream, cr_sqlite | planned |
| #699 graphui (P1) | react_flow, rete_js | planned |
| #700 education (P1) | lerobot, robopianist, pettingzoo, vizdoom | planned |
| #701 schedule (P1) | ray, temporal | planned |
| #702 kinematics (P1) | pinocchio, jolt_physics | planned |

The catalog is conservative by design: registration means **candidate only**, not available capability, deployment, benchmark approval, valid license or runtime activation.
