"""Read-only, opt-in LeRobotDataset v3.0 CPU trajectory importer (#700a).

No hub access, no video decode, no robot commands, no Noosphere reads.
pyarrow is an *optional* dependency used only by load_parquet_shard().
A shard is accepted only if it contains complete ordered episodes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA = "mojum.integration.lerobot-trajectory/1"
MAX_INFO_BYTES = 128 * 1024
MAX_SHARD_BYTES = 128 * 1024 * 1024
MAX_FRAMES = 8192
MAX_EPISODES = 128
MAX_DIM = 128
BATCH_SIZE = 128
COLUMNS = ("episode_index", "frame_index", "timestamp", "task_index",
           "observation.state", "action")
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?\\Z")
_REV = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}\\Z")
_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\\Z")


class LeRobotImportError(ValueError):
    """Rejected untrusted trajectory/metadata or unsupported format."""


@dataclass(frozen=True)
class Frame:
    episode_index: int
    frame_index: int
    timestamp: float
    task_index: int
    state: tuple[float, ...]
    action: tuple[float, ...]


@dataclass(frozen=True)
class Trajectory:
    schema: str
    dataset_id: str
    dataset_revision: str
    dataset_license: str
    metadata_sha256: str
    shard_sha256: str
    frames: tuple[Frame, ...]
    digest: str

    @property
    def episodes(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(f.episode_index for f in self.frames))


def _integer(x, name, max_value):
    if type(x) is not int or not 0 <= x <= max_value:
        raise LeRobotImportError(f"{name}: invalid bounded integer")
    return x


def _real(x, name):
    if type(x) not in (int, float):
        raise LeRobotImportError(f"{name}: expected finite numeric value")
    try:
        value = float(x)
    except (OverflowError, ValueError, TypeError) as exc:
        raise LeRobotImportError(f"{name}: invalid numeric value") from exc
    if not math.isfinite(value):
        raise LeRobotImportError(f"{name}: nonfinite numeric value")
    return value


def _shape(features, key):
    if type(features) is not dict or type(features.get(key)) is not dict:
        raise LeRobotImportError(f"missing feature {key}")
    feature = features[key]
    if feature.get("dtype") not in ("float32", "float64"):
        raise LeRobotImportError(f"unsupported dtype for {key}")
    shape = feature.get("shape")
    if (type(shape) is not list or len(shape) != 1
            or type(shape[0]) is not int or not 1 <= shape[0] <= MAX_DIM):
        raise LeRobotImportError(f"invalid bounded vector shape for {key}")
    return shape[0]


def validate_info(info):
    """Return (fps, state_dim, action_dim); no implicit v2/v3 reinterpretation."""
    if type(info) is not dict or info.get("codebase_version") != "v3.0":
        raise LeRobotImportError("only LeRobotDataset codebase_version v3.0 is supported")
    fps = _real(info.get("fps"), "fps")
    if not 0 < fps <= 1000:
        raise LeRobotImportError("fps outside supported CPU budget")
    features = info.get("features")
    return fps, _shape(features, "observation.state"), _shape(features, "action")


def _vector(value, size, name):
    if type(value) not in (list, tuple) or len(value) != size:
        raise LeRobotImportError(f"{name}: invalid vector length")
    return tuple(_real(x, name) for x in value)


def _source(dataset_id, dataset_revision, dataset_license):
    if type(dataset_id) is not str or not _SOURCE_ID.fullmatch(dataset_id):
        raise LeRobotImportError("dataset id must be a bounded public identifier")
    if type(dataset_revision) is not str or not _REV.fullmatch(dataset_revision):
        raise LeRobotImportError("dataset revision must be an exact 40/64-hex commit")
    if type(dataset_license) is not str or not _LICENSE.fullmatch(dataset_license):
        raise LeRobotImportError("dataset license must be an explicit bounded SPDX-like id")


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def import_rows(rows, info, *, dataset_id, dataset_revision, dataset_license,
                metadata_sha256, shard_sha256):
    """Normalize an iterable of *public* LeRobot v3 tabular rows, bounded.

    Primarily a stdlib contract/test seam; it never opens a dataset or moves a
    simulated body. All unknown columns (including video) are ignored.
    """
    fps, state_dim, action_dim = validate_info(info)
    _source(dataset_id, dataset_revision, dataset_license)
    for label, value in (("metadata", metadata_sha256), ("shard", shard_sha256)):
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise LeRobotImportError(f"{label} SHA-256 is required")
    frames = []
    prior = None
    episode_count = 0
    for row in rows:
        if len(frames) >= MAX_FRAMES:
            raise LeRobotImportError("trajectory frame budget exceeded")
        if type(row) is not dict or not all(key in row for key in COLUMNS):
            raise LeRobotImportError("missing required LeRobot frame columns")
        episode = _integer(row["episode_index"], "episode_index", 2**31 - 1)
        index = _integer(row["frame_index"], "frame_index", MAX_FRAMES)
        task = _integer(row["task_index"], "task_index", 2**31 - 1)
        timestamp = _real(row["timestamp"], "timestamp")
        if timestamp < 0 or abs(timestamp - index / fps) > max(.002, .15 / fps):
            raise LeRobotImportError("timestamp does not match episode frame/FPS")
        if prior is None or episode != prior.episode_index:
            if prior is not None and episode <= prior.episode_index:
                raise LeRobotImportError("episode indices must strictly increase")
            if index != 0:
                raise LeRobotImportError("partial episode shard not supported")
            episode_count += 1
            if episode_count > MAX_EPISODES:
                raise LeRobotImportError("episode budget exceeded")
        elif index != prior.frame_index + 1:
            raise LeRobotImportError("non-contiguous episode frame indices")
        frame = Frame(episode, index, timestamp, task,
                      _vector(row["observation.state"], state_dim, "state"),
                      _vector(row["action"], action_dim, "action"))
        frames.append(frame)
        prior = frame
    if not frames:
        raise LeRobotImportError("empty trajectory")
    payload = {
        "schema": SCHEMA, "dataset_id": dataset_id,
        "dataset_revision": dataset_revision, "dataset_license": dataset_license,
        "metadata_sha256": metadata_sha256, "shard_sha256": shard_sha256,
        "frames": [
            [f.episode_index, f.frame_index, f.timestamp, f.task_index,
             f.state, f.action] for f in frames
        ],
    }
    return Trajectory(SCHEMA, dataset_id, dataset_revision, dataset_license,
                      metadata_sha256, shard_sha256, tuple(frames),
                      hashlib.sha256(_json_bytes(payload)).hexdigest())


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LeRobotImportError("duplicate metadata JSON keys")
        result[key] = value
    return result


def _safe_local_file(root, relative):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise LeRobotImportError("dataset root must be a real local directory")
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise LeRobotImportError("unsafe dataset-relative path")
    resolved_root = root.resolve(strict=True)
    candidate = root / part
    if any(p.is_symlink() for p in (candidate, *candidate.parents)
           if p != candidate.anchor and p != resolved_root.parent):
        raise LeRobotImportError("dataset symlinks are not allowed")
    try:
        candidate.resolve(strict=True).relative_to(resolved_root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise LeRobotImportError("dataset path escapes root or is unavailable") from exc
    if not candidate.is_file():
        raise LeRobotImportError("dataset path is not a regular file")
    return candidate


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_parquet_shard(root, *, shard, dataset_id, dataset_revision,
                       dataset_license, allow_public_dataset=False):
    """Explicitly read an already-local v3 Parquet shard using optional pyarrow.

    No auto-install, network, executable code, video or robotics integration.
    Input data are caller-attested public and subject to independent license review.
    """
    if allow_public_dataset is not True:
        raise LeRobotImportError("explicit public-dataset opt-in is required")
    if (type(shard) is not str or not shard.startswith("data/")
            or not shard.endswith(".parquet")):
        raise LeRobotImportError("select a relative data/*.parquet shard")
    info_path = _safe_local_file(root, "meta/info.json")
    if info_path.stat().st_size > MAX_INFO_BYTES:
        raise LeRobotImportError("oversized LeRobot info.json")
    with info_path.open("rb") as handle:
        raw = handle.read(MAX_INFO_BYTES + 1)
    if len(raw) > MAX_INFO_BYTES:
        raise LeRobotImportError("oversized LeRobot info.json")
    try:
        info = json.loads(raw, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              LeRobotImportError("nonfinite metadata JSON")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LeRobotImportError("invalid LeRobot info.json") from exc
    validate_info(info)
    shard_path = _safe_local_file(root, shard)
    if not 0 < shard_path.stat().st_size <= MAX_SHARD_BYTES:
        raise LeRobotImportError("shard outside bounded byte budget")
    _source(dataset_id, dataset_revision, dataset_license)
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise LeRobotImportError(
            "optional pyarrow required for Parquet; install explicitly outside Mojum core"
        ) from exc
    reader = parquet.ParquetFile(str(shard_path))
    if reader.metadata.num_rows > MAX_FRAMES:
        raise LeRobotImportError("Parquet row count exceeds frame budget")
    if not set(COLUMNS).issubset(reader.schema_arrow.names):
        raise LeRobotImportError("Parquet missing required tabular columns")
    def rows():
        for batch in reader.iter_batches(batch_size=BATCH_SIZE, columns=list(COLUMNS)):
            yield from batch.to_pylist()
    return import_rows(rows(), info, dataset_id=dataset_id,
                       dataset_revision=dataset_revision, dataset_license=dataset_license,
                       metadata_sha256=hashlib.sha256(raw).hexdigest(),
                       shard_sha256=_hash_file(shard_path))


def heldout_zero_baseline(trajectory, *, seed=0):
    """Deterministic episodic train/holdout split vs frozen zero-action control.

    Diagnostic only: a train-set constant action mean is NOT a learned policy.
    """
    if not isinstance(trajectory, Trajectory) or trajectory.schema != SCHEMA:
        raise LeRobotImportError("expected imported trajectory")
    _integer(seed, "seed", 2**32 - 1)
    episodes = trajectory.episodes
    if len(episodes) < 2:
        raise LeRobotImportError("at least two complete episodes needed for holdout")
    ordered = sorted(episodes, key=lambda ep: (
        hashlib.sha256(f"{seed}:{ep}".encode("ascii")).digest(), ep))
    heldout = set(ordered[:max(1, len(episodes) // 4)])
    train = [f for f in trajectory.frames if f.episode_index not in heldout]
    test = [f for f in trajectory.frames if f.episode_index in heldout]
    dim = len(train[0].action)
    mean = tuple(sum(f.action[j] for f in train) / len(train) for j in range(dim))
    frozen_mse = sum(sum(a*a for a in f.action) for f in test) / (len(test)*dim)
    constant_mse = sum(sum((a-mean[j])**2 for j, a in enumerate(f.action))
                       for f in test) / (len(test)*dim)
    return {
        "schema": "mojum.eval.lerobot-frozen-holdout/1",
        "source_digest": trajectory.digest, "seed": seed,
        "train_episodes": sorted(set(episodes) - heldout),
        "heldout_episodes": sorted(heldout),
        "train_frames": len(train), "heldout_frames": len(test),
        "frozen_zero_action_mse": frozen_mse,
        "train_constant_mean_action_mse": constant_mse,
        "learned_motor_policy": False, "physical_contact": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="existing local LeRobotDataset v3 directory")
    parser.add_argument("--shard", required=True, help="relative data/...parquet")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--revision", required=True, help="exact 40/64-hex dataset commit")
    parser.add_argument("--license", required=True, help="dataset license identifier (not clearance)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--allow-public-dataset", action="store_true")
    args = parser.parse_args(argv)
    imported = load_parquet_shard(
        args.root, shard=args.shard, dataset_id=args.dataset_id,
        dataset_revision=args.revision, dataset_license=args.license,
        allow_public_dataset=args.allow_public_dataset)
    summary = {
        "schema": SCHEMA, "digest": imported.digest,
        "metadata_sha256": imported.metadata_sha256,
        "shard_sha256": imported.shard_sha256,
        "episodes": len(imported.episodes), "frames": len(imported.frames),
        "benchmark": (heldout_zero_baseline(imported, seed=args.seed)
                      if len(imported.episodes) >= 2 else None),
    }
    print(json.dumps(summary, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
