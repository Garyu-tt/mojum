# #700(a): LeRobot v3 numeric rows, opt-in offline import (R0.1)

This is an **isolated, stdlib-only LeRobot Dataset v3.0 numeric-row export consumer**. It does **not** read native Parquet, decode MP4, download datasets, issue robot commands, drive GENESIS/Aquarium motor policy, access Noosphere, or establish provenance/permissions. Importing a dataset or having a license string is NOT proof of consent or ownership. Run only on synthetic or explicitly authorized data in a separate directory.

## Inputs

LeRobot v3.0 stores numeric state/action/timestamps in Parquet shards and metadata in JSON/Parquet. After a separate, explicitly authorized conversion to JSONL, this adapter accepts only `index`, `episode_index`, `frame_index`, `timestamp`, `task_index`, `observation.state`, and `action`. One ordered JSON object per line. All episodes contiguous, globally indexed from zero, with each episode starting at local frame zero. Timestamps start near zero and strictly increase within episodes. No video, task prose, raw identifiers, URLs, joints/physical calibration, or arbitrary columns are accepted. The `observation.state` and `action` vectors are numeric arrays of fixed length (1..128).

Provide a manifest (example below; replace the fake SHA with the real hash and the dummy revision with an authenticated source commit):

```json
{
  "schema": "mojum.lerobot-export/1",
  "lerobot_format": "v3.0",
  "repo_id": "synthetic/held-out",
  "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "license": "CC0-1.0",
  "fps": 10,
  "state_dim": 1,
  "action_dim": 1,
  "frames_sha256": "<SHA256 of exact frames.jsonl bytes>"
}
```

The revision must be an exact 40-character lowercase commit hex. The manifest's `license` is **unverified source-supplied metadata**, not a security authorization. Dataset assets, model weights, videos and metadata licenses can differ from LeRobot's code license; check upstream dataset/model card before use. Input files must be created *outside* PRIVATE journal directories. Native conversion is deliberately out of this stdlib/core PR; if using LeRobot's upstream reader, run it separately with the audited/pinned `lerobot==0.7.0` dependency and your verified local dataset snapshot. Do not allow the exporter to actuate hardware or load private organism data.

```sh
PYTHONPATH=src python -m mojum.lerobot_jsonl_export \
  --manifest examples/lerobot_offline/manifest.json \
  --frames examples/lerobot_offline/frames.jsonl --held-out-control
PYTHONPATH=src python -m unittest discover -s tests -p 'test_lerobot_jsonl_export.py' -v
```

Only numeric summary data and source revision/checksum are emitted; no state/action values, raw paths, task strings or PRIVATE records are printed. Source revision is metadata, not a signature. SHA-256 protects export integrity **relative to the supplied manifest**, not authenticity; independently verify the manifest against a trusted source. Do not publish its content without authorization.

## Repeatable synthetic evaluation

`--held-out-control` fits independent per-dimension scalar gains from state differences in **all but the last** episode. In the last episode only, compare the state-delta MSE with a frozen zero-delta predictor. It does not learn a robotics skill, train LeRobot, act in the Aquarium, or prove generalization. Evaluation requires at least two episodes, matching action/state dimensions and at least one eligible transition in both splits. Never mix adjacent frames from different episodes. Each run is deterministic for identical serialized bytes.

## Bounds and failures

- Manifest max 4 KiB; frames file max 4 MiB, max 4,096 rows, 64 episodes, 8 KiB per record, up to 128 state/action dimensions. CPU-only; bounded in-memory representation. No dependencies beyond Python 3.11+ stdlib. No auto-import into SQLite, Noosphere or Graph IR.
- Refuse unknown/duplicate JSON fields, NaN/Infinity/bools as numbers, non-monotonic time, missing indices, interleaved episodes, zero rows, unpinned revisions, version mismatches, invalid vector shapes and raw-byte checksum mismatch. Failures provide generic messages without echoing sensitive content.
- Disk and peak RAM budgets: input at most ~4 MiB, parsed object allocations may exceed this; verify on the target Python/runtime before raising the cap. No tested real-time guarantees. The offline adapter is not a substitute for native v3 Parquet + MP4 decoding or joint/DOF normalization.

## Upstream / license gate

- LeRobot toolkit code: Apache-2.0, https://github.com/huggingface/lerobot/blob/main/LICENSE (check at pinned upstream code revision on deployment).
- Reference toolkit release to assess: **LeRobot v0.7.0**; reference dataset wire format: **LeRobotDataset v3.0**, https://huggingface.co/docs/lerobot/lerobot-dataset-v3 .
- Version 3 uses Parquet/MP4 shards with metadata-based episode lookup. The JSONL import here is a **Mojum-specific, loss-restricted interchange layer**, NOT a claim to read a native LeRobotDataset directory. Keep data access and provenance authorization external until explicit review.
- Release gate: independent QA, exact PR head and Python 3.11–3.13 CI, no direct merge.
