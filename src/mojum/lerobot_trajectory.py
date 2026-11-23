"""Offline, opt-in validation of explicitly exported LeRobot v3 numeric rows.

This is NOT a Parquet/MP4 reader and never sends motor commands or accesses journals.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA = "mojum.lerobot-export/1"
MAX_MANIFEST_BYTES = 4096
MAX_FRAMES_BYTES = 4 * 1024 * 1024
MAX_ROWS = 4096
MAX_EPISODES = 64
MAX_DIM = 128
_ROW_KEYS = frozenset(("index", "episode_index", "frame_index", "timestamp", "task_index", "observation.state", "action"))
_MANIFEST_KEYS = frozenset(("schema", "lerobot_format", "repo_id", "revision", "license", "fps", "state_dim", "action_dim", "frames_sha256"))


class TrajectoryError(ValueError):
    """Untrusted export is invalid; safe for callers to catch without logging rows."""


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TrajectoryError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise TrajectoryError("nonfinite JSON number")


def _json(raw: str):
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TrajectoryError("invalid JSON record") from exc


def _read_bounded(path: str | Path, limit: int) -> bytes:
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise TrajectoryError("export size limit exceeded")
    return data


def _integer(value, name: str, minimum: int = 0, maximum: int = 2**31 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TrajectoryError(f"invalid {name}")
    return value


def _number(value, name: str, minimum: float = -1e6, maximum: float = 1e6) -> float:
    if type(value) not in (int, float):
        raise TrajectoryError(f"invalid {name}")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise TrajectoryError(f"invalid {name}") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise TrajectoryError(f"invalid {name}")
    return number


def _vector(value, name: str, dimension: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != dimension:
        raise TrajectoryError(f"invalid {name} dimensions")
    return tuple(_number(x, name) for x in value)


@dataclass(frozen=True)
class Frame:
    index: int
    episode_index: int
    frame_index: int
    timestamp: float
    task_index: int
    state: tuple[float, ...]
    action: tuple[float, ...]


@dataclass(frozen=True)
class Trajectory:
    frames: tuple[Frame, ...]
    repo_id: str
    revision: str
    license: str
    frames_sha256: str
    fps: float
    state_dim: int
    action_dim: int

    @property
    def episode_count(self) -> int:
        return 1 + self.frames[-1].episode_index - self.frames[0].episode_index

    def summary(self) -> dict:
        # Deliberately omit source state, task descriptions, device IDs and raw file paths.
        return {"schema": SCHEMA, "source_revision": self.revision,
                "frames_sha256": self.frames_sha256, "rows": len(self.frames),
                "episodes": self.episode_count, "state_dim": self.state_dim,
                "action_dim": self.action_dim}


def load_export(manifest_path: str | Path, rows_path: str | Path) -> Trajectory:
    """Validate synthetic/authorized v3 numeric JSONL exported separately from LeRobot.

    Both files must be explicitly supplied. No native LeRobot modules are imported.
    The manifest checksum covers the exact bytes in the row export.
    """
    try:
        manifest = _json(_read_bounded(manifest_path, MAX_MANIFEST_BYTES).decode("utf-8"))
    except UnicodeError as exc:
        raise TrajectoryError("manifest is not UTF-8") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise TrajectoryError("invalid manifest fields")
    if manifest["schema"] != SCHEMA or manifest["lerobot_format"] != "v3.0":
        raise TrajectoryError("unsupported export schema or LeRobot dataset format")
    repo_id = manifest["repo_id"]
    if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", repo_id):
        raise TrajectoryError("invalid repository identifier")
    revision = manifest["revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise TrajectoryError("dataset source must pin a 40-hex revision")
    license_id = manifest["license"]
    if not isinstance(license_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", license_id):
        raise TrajectoryError("dataset license identifier required")
    fps = _number(manifest["fps"], "fps", 0.01, 1000)
    state_dim = _integer(manifest["state_dim"], "state_dim", 1, MAX_DIM)
    action_dim = _integer(manifest["action_dim"], "action_dim", 1, MAX_DIM)
    expected_digest = manifest["frames_sha256"]
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise TrajectoryError("invalid frames checksum")
    data = _read_bounded(rows_path, MAX_FRAMES_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_digest:
        raise TrajectoryError("frames checksum mismatch")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise TrajectoryError("frames are not UTF-8") from exc
    if not 1 <= len(lines) <= MAX_ROWS:
        raise TrajectoryError("invalid frame count")
    result = []
    last_episode = None
    last_timestamp = -1.0
    episode_count = 0
    for offset, line in enumerate(lines):
        if len(line.encode("utf-8")) > 8192:
            raise TrajectoryError("oversized frame record")
        row = _json(line)
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise TrajectoryError("invalid frame fields")
        index = _integer(row["index"], "index")
        episode = _integer(row["episode_index"], "episode_index")
        local = _integer(row["frame_index"], "frame_index")
        task = _integer(row["task_index"], "task_index")
        stamp = _number(row["timestamp"], "timestamp", 0, 1e7)
        state = _vector(row["observation.state"], "state", state_dim)
        action = _vector(row["action"], "action", action_dim)
        if index != offset:
            raise TrajectoryError("non-contiguous global index")
        if last_episode is None or episode != last_episode:
            if last_episode is not None and episode != last_episode + 1:
                raise TrajectoryError("non-contiguous or interleaved episodes")
            if local != 0 or stamp > 1.0 / fps:
                raise TrajectoryError("episode must begin at frame zero and timestamp near zero")
            episode_count += 1
            if episode_count > MAX_EPISODES:
                raise TrajectoryError("episode count limit exceeded")
        elif local != result[-1].frame_index + 1 or stamp <= last_timestamp:
            raise TrajectoryError("non-monotonic local frame or timestamp")
        result.append(Frame(index, episode, local, stamp, task, state, action))
        last_episode, last_timestamp = episode, stamp
    return Trajectory(tuple(result), repo_id, revision, license_id, digest, fps, state_dim, action_dim)


def held_out_control(trajectory: Trajectory) -> dict:
    """Offline scalar-gain proxy versus frozen zero-delta control; never motor policy.

    Fits on all but final episode; evaluates on final episode only. Actions at
    timestep t predict observed state delta between t and t+1.
    """
    if trajectory.episode_count < 2 or trajectory.state_dim != trajectory.action_dim:
        raise TrajectoryError("held-out proxy requires >=2 episodes and matching dimensions")
    holdout = trajectory.frames[-1].episode_index
    num, den = [0.] * trajectory.state_dim, [0.] * trajectory.state_dim
    pairs = []
    for left, right in zip(trajectory.frames, trajectory.frames[1:]):
        if left.episode_index != right.episode_index:
            continue
        (pairs if left.episode_index == holdout else []).append((left, right))
        if left.episode_index != holdout:
            for j, action in enumerate(left.action):
                num[j] += action * (right.state[j] - left.state[j])
                den[j] += action * action
    if not pairs or all(d == 0 for d in den):
        raise TrajectoryError("no usable train or held-out transition pairs")
    gain = [n / d if d > 0 else 0. for n, d in zip(num, den)]
    baseline = fitted = 0.
    for left, right in pairs:
        for j, action in enumerate(left.action):
            delta = right.state[j] - left.state[j]
            baseline += delta * delta
            fitted += (delta - gain[j] * action) ** 2
    count = len(pairs) * trajectory.state_dim
    return {"held_out_episode": holdout, "train_pairs": sum(
                a.episode_index != holdout and a.episode_index == b.episode_index
                for a, b in zip(trajectory.frames, trajectory.frames[1:])),
            "held_out_pairs": len(pairs), "frozen_zero_mse": baseline / count,
            "fitted_gain_mse": fitted / count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline LeRobot v3 numeric export audit; no motor execution")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--held-out-control", action="store_true")
    args = parser.parse_args(argv)
    try:
        trajectory = load_export(args.manifest, args.frames)
        summary = trajectory.summary()
        if args.held_out_control:
            summary["proxy_eval"] = held_out_control(trajectory)
    except (TrajectoryError, OSError) as exc:
        parser.exit(2, "LeRobot export rejected: " + type(exc).__name__ + "\n")
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
