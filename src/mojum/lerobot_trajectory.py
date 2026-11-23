"""Opt-in LeRobot v3 *decoded-row* bridge for offline imitation experiments.

Does not download datasets, read Parquet, import torch or control a robot.
Only five allowlisted fields survive from caller-supplied LeRobot rows.
Provenance is an explicit caller assertion, NOT license/consent verification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from itertools import islice

SCHEMA = "mojum.integration.lerobot-trajectory/1"
BENCHMARK_SCHEMA = "mojum.eval.lerobot-heldout/1"
MAX_FRAMES = 2048
MAX_AXES = 128
MAX_EPISODES = 64
MAX_BENCHMARK_FRAMES = 512
_MAX_ABS = 1_000_000.0
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,95}\Z", re.ASCII)
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z", re.ASCII)


class TrajectoryError(ValueError):
    """Invalid, oversized, ambiguous or unsafe offline demonstration."""


def _id(value: str, key: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise TrajectoryError(f"{key}: invalid bounded identifier")
    return value


def _revision(value: str, key: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise TrajectoryError(f"{key}: expected immutable hexadecimal revision")
    return value


def _number(value: object, key: str, *, nonnegative: bool = False) -> float:
    if type(value) not in (int, float):
        raise TrajectoryError(f"{key}: finite numeric value required")
    if abs(value) > _MAX_ABS or (nonnegative and value < 0) or not math.isfinite(value):
        raise TrajectoryError(f"{key}: value out of bounds")
    return float(value)


def _index(value: object, key: str) -> int:
    if type(value) is not int or not (0 <= value < 2**31):
        raise TrajectoryError(f"{key}: nonnegative int32 required")
    return value


def _vector(value: object, key: str) -> tuple[float, ...]:
    if type(value) not in (tuple, list) or not 1 <= len(value) <= MAX_AXES:
        raise TrajectoryError(f"{key}: bounded 1D list/tuple required")
    return tuple(_number(v, key) for v in value)


@dataclass(frozen=True)
class Provenance:
    dataset_id: str
    dataset_revision: str
    robot_asset_sha256: str
    license_id: str
    collector: str  # "teleoperator" or "policy"
    consent_asserted: bool
    model_id: str | None = None
    model_revision: str | None = None

    def __post_init__(self) -> None:
        _id(self.dataset_id, "dataset_id")
        _revision(self.dataset_revision, "dataset_revision")
        if type(self.robot_asset_sha256) is not str or _SHA256.fullmatch(self.robot_asset_sha256) is None:
            raise TrajectoryError("robot_asset_sha256: expected sha256")
        if type(self.license_id) is not str or _LICENSE.fullmatch(self.license_id) is None:
            raise TrajectoryError("license_id: bounded SPDX-like token required")
        if self.consent_asserted is not True:
            raise TrajectoryError("explicit source consent assertion required")
        if type(self.collector) is not str:
            raise TrajectoryError("collector: expected teleoperator or policy")
        if self.collector == "teleoperator":
            if self.model_id is not None or self.model_revision is not None:
                raise TrajectoryError("teleoperator cannot claim a policy model")
        elif self.collector == "policy":
            _id(self.model_id, "model_id")
            _revision(self.model_revision, "model_revision")
        else:
            raise TrajectoryError("collector: expected teleoperator or policy")


@dataclass(frozen=True)
class Frame:
    timestamp: float
    frame_index: int
    state: tuple[float, ...]
    action: tuple[float, ...]


@dataclass(frozen=True)
class Trajectory:
    provenance: Provenance
    episode_index: int
    frames: tuple[Frame, ...]
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != SCHEMA or type(self.provenance) is not Provenance:
            raise TrajectoryError("invalid trajectory schema/provenance")
        _index(self.episode_index, "episode_index")
        if type(self.frames) is not tuple or not 1 <= len(self.frames) <= MAX_FRAMES:
            raise TrajectoryError("invalid trajectory frame tuple")
        state_dim = action_dim = None
        last = None
        for frame in self.frames:
            if type(frame) is not Frame:
                raise TrajectoryError("invalid frame type")
            _index(frame.frame_index, "frame_index")
            _number(frame.timestamp, "timestamp", nonnegative=True)
            _vector(frame.state, "state")
            _vector(frame.action, "action")
            if last is not None and (frame.frame_index != last.frame_index + 1 or
                                     frame.timestamp <= last.timestamp):
                raise TrajectoryError("nonconsecutive/unsorted trajectory frames")
            if state_dim is None:
                state_dim, action_dim = len(frame.state), len(frame.action)
            elif len(frame.state) != state_dim or len(frame.action) != action_dim:
                raise TrajectoryError("varying trajectory dimensions")
            last = frame

    @property
    def digest(self) -> str:
        """Deterministic content checksum; not a signature or authenticity proof."""
        payload = {
            "schema": self.schema,
            "provenance": vars(self.provenance),
            "episode_index": self.episode_index,
            "frames": [vars(frame) for frame in self.frames],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def import_decoded_rows(rows: Iterable[Mapping], *, provenance: Provenance,
                        episode_index: int, max_frames: int = MAX_FRAMES) -> Trajectory:
    """Validate bounded single-episode LeRobot v3 row dictionaries.

    Only episode_index, frame_index, timestamp, observation.state, action
    survive. Images, tasks, paths, other data and PRIVATE fields are ignored.
    """
    if type(provenance) is not Provenance:
        raise TrajectoryError("explicit Provenance required")
    _index(episode_index, "episode_index")
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise TrajectoryError("max_frames out of bounds")
    if not isinstance(rows, Iterable):
        raise TrajectoryError("decoded rows must be iterable")
    frames: list[Frame] = []
    state_dim = action_dim = None
    for row in rows:
        if len(frames) >= max_frames:
            raise TrajectoryError("trajectory frame budget exceeded")
        if not isinstance(row, Mapping):
            raise TrajectoryError("decoded row must be a mapping")
        try:
            episode = _index(row["episode_index"], "episode_index")
            index = _index(row["frame_index"], "frame_index")
            timestamp = _number(row["timestamp"], "timestamp", nonnegative=True)
            state = _vector(row["observation.state"], "observation.state")
            action = _vector(row["action"], "action")
        except KeyError as exc:
            raise TrajectoryError("missing required LeRobot field") from exc
        if episode != episode_index:
            raise TrajectoryError("mixed episode_index in trajectory")
        if frames:
            if index != frames[-1].frame_index + 1:
                raise TrajectoryError("nonconsecutive frame_index")
            if timestamp <= frames[-1].timestamp:
                raise TrajectoryError("timestamps must strictly increase")
            if len(state) != state_dim or len(action) != action_dim:
                raise TrajectoryError("state/action dimensionality changed")
        else:
            state_dim, action_dim = len(state), len(action)
        frames.append(Frame(timestamp, index, state, action))
    if not frames:
        raise TrajectoryError("empty episode")
    return Trajectory(provenance, episode_index, tuple(frames))


def _tensor_vector(value: object, key: str) -> tuple[float, ...]:
    """Optional torch/NumPy-like vector, bounded before tolist."""
    if type(value) in (list, tuple):
        return _vector(value, key)
    shape = getattr(value, "shape", None)
    if (shape is None or len(shape) != 1 or
            type(shape[0]) is not int or not 1 <= shape[0] <= MAX_AXES):
        raise TrajectoryError(f"{key}: unsupported tensor shape")
    tolist = getattr(value, "tolist", None)
    if not callable(tolist):
        raise TrajectoryError(f"{key}: explicitly decoded vector required")
    return _vector(tolist(), key)


def _tensor_scalar(value: object, key: str) -> object:
    if type(value) in (int, float):
        return value
    if getattr(value, "shape", None) != ():
        raise TrajectoryError(f"{key}: expected scalar shape")
    item = getattr(value, "item", None)
    if not callable(item):
        raise TrajectoryError(f"{key}: explicit numeric scalar required")
    return item()


def import_lerobot_dataset(dataset: object, *, provenance: Provenance,
                           episode_index: int, start: int, stop: int) -> Trajectory:
    """Supply a locally available LeRobotDataset-like object, never download.

    Caller explicitly selects a bounded single-episode [start, stop) slice.
    """
    if type(start) is not int or type(stop) is not int or not (0 <= start < stop):
        raise TrajectoryError("invalid bounded dataset slice")
    if stop > 2**31 or stop - start > MAX_FRAMES:
        raise TrajectoryError("dataset slice exceeds frame budget")
    if not hasattr(dataset, "__getitem__"):
        raise TrajectoryError("dataset must support indexed local rows")

    def decoded():
        for offset in range(start, stop):
            record = dataset[offset]
            if not isinstance(record, Mapping):
                raise TrajectoryError("dataset returned non-mapping row")
            try:
                yield {
                    "episode_index": _tensor_scalar(record["episode_index"], "episode_index"),
                    "frame_index": _tensor_scalar(record["frame_index"], "frame_index"),
                    "timestamp": _tensor_scalar(record["timestamp"], "timestamp"),
                    "observation.state": _tensor_vector(record["observation.state"], "observation.state"),
                    "action": _tensor_vector(record["action"], "action"),
                }
            except KeyError as exc:
                raise TrajectoryError("dataset missing required LeRobot field") from exc

    return import_decoded_rows(decoded(), provenance=provenance, episode_index=episode_index)


def replay(trajectory: Trajectory) -> tuple[tuple[float, tuple[float, ...], tuple[float, ...]], ...]:
    """Passive immutable measured-state/action log, never motor actuation."""
    if type(trajectory) is not Trajectory or trajectory.schema != SCHEMA:
        raise TrajectoryError("unsupported trajectory")
    return tuple((f.timestamp, f.state, f.action) for f in trajectory.frames)


def benchmark(episodes: Iterable[Trajectory], *, seed: int = 0) -> dict:
    """Fixed-seed episode-heldout nearest-state imitation vs frozen zero prior.

    Not a neural model, robot policy deployment or causal movement evaluation.
    """
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise TrajectoryError("seed: uint32 required")
    episodes = tuple(islice(episodes, MAX_EPISODES + 1))
    if not 3 <= len(episodes) <= MAX_EPISODES:
        raise TrajectoryError("benchmark needs 3..64 episodes")
    if any(type(ep) is not Trajectory or ep.schema != SCHEMA for ep in episodes):
        raise TrajectoryError("invalid trajectory input")
    if any(not ep.frames or len(ep.frames) > MAX_FRAMES for ep in episodes):
        raise TrajectoryError("empty or oversized benchmark trajectory")
    if sum(len(ep.frames) for ep in episodes) > MAX_BENCHMARK_FRAMES:
        raise TrajectoryError("benchmark total frame budget exceeded")
    keys = [(ep.provenance.dataset_id, ep.provenance.dataset_revision, ep.episode_index)
            for ep in episodes]
    if len(set(keys)) != len(keys):
        raise TrajectoryError("duplicate train/heldout episode identity")
    if len({(len(ep.frames[0].state), len(ep.frames[0].action)) for ep in episodes}) != 1:
        raise TrajectoryError("incompatible action/state dimensions")
    if len({ep.provenance.dataset_id for ep in episodes}) != 1:
        raise TrajectoryError("mixed datasets cannot share a benchmark")

    # Stable partition independent of caller's episode ordering.
    ordered = sorted(episodes, key=lambda ep: hashlib.sha256(
        f"{seed}:{ep.provenance.dataset_id}:{ep.provenance.dataset_revision}:{ep.episode_index}".encode()
    ).hexdigest())
    n_holdout = max(1, len(ordered) // 3)
    heldout, training = ordered[:n_holdout], ordered[n_holdout:]
    exemplars = tuple((f.state, f.action) for ep in training for f in ep.frames)
    n_actions = len(exemplars[0][1])
    trained_error = frozen_error = 0.0
    count = 0
    for ep in heldout:
        for frame in ep.frames:
            predicted = min(exemplars, key=lambda sa: sum(
                (a - b) ** 2 for a, b in zip(sa[0], frame.state)
            ))[1]
            trained_error += sum((a - b) ** 2 for a, b in zip(predicted, frame.action))
            frozen_error += sum(a * a for a in frame.action)
            count += n_actions
    return {
        "schema": BENCHMARK_SCHEMA,
        "scope": "offline_decoded_rows_not_robot_or_neural_learning",
        "seed": seed,
        "training_episodes": len(training),
        "heldout_episodes": len(heldout),
        "heldout_actions": count,
        "nearest_state_mse": trained_error / count,
        "frozen_zero_mse": frozen_error / count,
        "training_digests": [ep.digest for ep in training],
        "heldout_digests": [ep.digest for ep in heldout],
    }
