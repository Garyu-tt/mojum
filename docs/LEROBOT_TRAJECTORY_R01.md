# LeRobot decoded-trajectory offline bridge (issue #700a, experimental R0.1)

`mojum.lerobot_trajectory` is an **opt-in**, stdlib-only observation/action **import and evaluation** fixture. It does not install LeRobot, read raw Parquet/video, download data, actuate hardware, write canonical Noosphere journals, train GENESIS, or turn demonstrations into executable behavior. The bridge accepts already-decoded LeRobot v3 rows with fields `episode_index`, `frame_index`, `timestamp`, `observation.state`, and `action`; discards every additional field (especially cameras, credentials and PRIVATE data); copies bounded finite numeric 1D arrays into immutable tuples.

Upstream: [LeRobotDataset v3.0](https://huggingface.co/docs/lerobot/lerobot-dataset-v3) uses Parquet rows for state/action/timestamps plus separately stored video and `meta/info.json` for schema/FPS. LeRobot upstream is Apache-2.0 (check exact pinned upstream revision and transitive asset licenses before distributing). This module contains no vendored LeRobot code and requires no third-party runtime. Future optional Parquet decoding must happen outside the stdlib core with an explicitly supplied local dataset; the adapter never calls the Hub.

## Example: synthetic, offline, no hardware

```python
from mojum.lerobot_trajectory import Provenance, import_decoded_rows, replay

proof = Provenance(dataset_id="synthetic/example", dataset_revision="a" * 40,
    robot_asset_sha256="b" * 64, license_id="CC0-1.0",
    collector="teleoperator", consent_asserted=True)
rows = [dict(episode_index=0, frame_index=i, timestamp=i / 30.,
             **{"observation.state": [float(i)], "action": [.25]})
        for i in range(3)]
trajectory = import_decoded_rows(rows, provenance=proof, episode_index=0)
print(trajectory.digest, replay(trajectory))
```

An external **user-installed** `LeRobotDataset` object can be supplied to `import_lerobot_dataset(dataset, provenance=..., episode_index=0, start=0, stop=30)`; the caller must provision an *offline*, locally present, appropriately licensed dataset and select a bounded **single-episode** slice. The bridge converts 1D `torch.Tensor` / ndarray-like `tolist()` values with shape preflight. Revision, asset SHA, model identity, license and consent are caller *assertions*, not verified ownership, attestation or legal clearance. For policy-collected demonstrations supply `collector="policy"`, `model_id`, `model_revision`; for teleoperation omit both model fields. No actual private organism state or real actuator should be fed into the adapter.

## Replay and controlled comparison

`replay(trajectory)` returns an immutable tuple of (timestamp, observation.state, recorded action). It has no action executor. `benchmark(episodes, seed=42)` hashes immutable dataset/episode identities to split entire episodes into training vs heldout; a deterministic nearest-state, non-gradient imitation **reference** is evaluated against a truly frozen zero-action prior on the same heldout frames. It returns MSE on recorded actions, **not** control performance or a learned neuro-motor policy. At least 3 and at most 64 episodes, ≤2048 frames per episode, **≤512 total benchmark frames**, ≤128 numbers per state/action, finite scalar magnitude ≤1e6, 32-bit nonnegative indices, strict increasing timestamps and explicit fixed provenance are required. Failures reject the entire import or evaluation; no partial persistence.

Run: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_lerobot_trajectory.py' -v`.

## Gates and limits

- Unknown LeRobot row fields are ignored rather than copied. Neither video, audio, path, free-text task, private prompt nor dataset credentials survive in output.
- Caller retains responsibility for source provenance verification and permission. Data may be sensitive even when arrays are numeric; never hand it PRIVATE records or enable cloud fetching.
- All read-only fixtures; no default import/dependency, network request, live motor control, SQLite migration, or Graph IR mutation.
- Benchmark fixture and bounded-in-process timing can be run on CPU but no GPU/Pixel throughput or full Parquet decoder claim is made. Independent exact-head CI and license/asset review are still required before promotion.
