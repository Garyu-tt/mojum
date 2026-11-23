# LeRobot v3.0 read-only trajectory bridge, experimental (#700a)

**Status: opt-in experiment, not an accepted Mojum runtime integration.**
Only LeRobot slice **(a)** in issue #700. Other workers own PettingZoo and other optional benchmarks.

This reader accepts an **already downloaded, explicitly public** LeRobotDataset
**v3.0 Parquet data shard**, with `meta/info.json` alongside it. No LeRobot,
PyTorch, HF Hub client, camera, device, Noosphere, network, robot control or
model execution is imported by default. The stdlib test seam accepts in-memory
synthetic LeRobot-shaped dictionaries. Actual Parquet requires an independently
installed `pyarrow`, only when the user explicitly runs the reader.

## Usage

```bash
# Optional tool ONLY; do not install this into the base Mojum environment.
python -m pip install 'pyarrow'  # in a separate, isolated benchmark venv
PYTHONPATH=src python -m mojum.lerobot_trajectory ./public-lerobot-v3 \
  --shard data/chunk-000/file-000.parquet \
  --dataset-id public/demo --revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --license Apache-2.0 --seed 123 --allow-public-dataset
```

The commit hash, public dataset ID and license in the sample command are
**placeholders**, not trusted source provenance. Replace them with the dataset's
actual immutable Hub revision and independently checked license. The flag is
a caller assertion, **not** verification that the material is lawfully public.

The JSON output contains only source digests, counts, reproducible split IDs
and summary error scores. In-process `Trajectory.frames` contains typed,
bounded numerical observations/actions for use by future *read-only*
benchmarks. **Actions are never applied to a body.** No MP4 frames,
tasks text, unknown columns or local absolute paths are copied into output.

## Exact constraints and deliberate refusals

- `meta/info.json`: `codebase_version: "v3.0"`, fps and scalar vector
  `action` + `observation.state` features with 1–128 finite float values.
  Unknown metadata is ignored, never executed. Duplicate keys are rejected.
- Tabular columns: `episode_index`, `frame_index`, `timestamp`,
  `task_index`, `observation.state`, `action`. No default camera/video
  loading, even when image columns exist in the file.
- Each accepted shard must have **contiguous episode segments starting at**
  `frame_index=0`, and strictly ordered episode IDs. LeRobot v3.0 permits
  multi-episode shards and episodes spanning shards. **Shards with an incomplete
  leading episode are refused**. A final segment **may be incomplete**, because
  episode-length metadata is not yet joined in this first slice; metrics are
  only shard-segment diagnostics, **not** full-episode evaluations. This is
  not a full LeRobotDataset API replacement or multi-shard assembler.
- Timestamp drift from `frame_index/fps` must be within the documented
  tolerance in code. Atypical irregularly sampled datasets are refused.
- Hard bounds: at most 128 KiB metadata, 128 MiB compressed shard, 8192 frame
  rows, 128 episodes, 128 elements per vector, 128 rows per read batch.
  Always use an unprivileged benchmark process for potentially untrusted
  compressed Parquet; compressed-size caps do not prove an absolute decoded
  memory cap.
- Local paths only under the explicitly provided root: no `..`, absolute
  paths or symlinks. Only the `data/*.parquet` subtree is eligible.
- Digest pins: SHA-256 of local metadata and compressed shard are included
  in replay provenance, alongside an immutable 40/64-hex dataset revision.
  Optional `--asset-sha256` and `--model-sha256` pin independently obtained
  asset/model content digests; neither models nor assets are loaded.
  Digests prove byte *consistency*, not license, consent or authenticity.
- The held-out benchmark chooses disjoint episode IDs with SHA-256(seed, id);
  it compares a truly frozen **zero-action** baseline against a constant action
  mean fitted on training episodes only. It deliberately **does not claim**
  autonomous learning, imitation capability or motor safety.
- No changes to canonical Graph IR, PRIVATE Noosphere, organism identity,
  existing journals, local policy or on-device behavior.

## Reproducible checks

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lerobot_trajectory.py' -v
PYTHONPATH=src python -m unittest discover -s tests
```

The stdlib suite uses a narrow `pyarrow.parquet.ParquetFile` test double to
verify the selected-column/batched Parquet API and negative path/schema cases.
**It does not certify real installed PyArrow or a real v3 dataset.** A follow-up
independent test must install pinned PyArrow in an isolated job, generate/read
real Parquet, cross-check with `lerobot >= 0.4.0` and measure wall CPU/RSS,
before this candidate is eligible for acceptance.

Upstream format: https://huggingface.co/docs/lerobot/lerobot-dataset-v3
Upstream implementation/license: https://github.com/huggingface/lerobot
LeRobot's code is Apache-2.0, but each dataset, recording and model has its
**own** licensing/consent obligations. This adapter imports no upstream code,
models or assets and leaves the integration catalog's `license_review`
at `required`.

## Future work, separately reviewed

Incremental multi-shard episode reassembly using v3 `meta/episodes` offsets,
true Parquet CI and resource measurements, plus optional timestamped
demonstration-to-GENESIS typed observation mapping behind an explicit
motor-safety gate. No automatic action replay or simulated hardware claims.
