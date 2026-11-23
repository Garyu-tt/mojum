# LeRobot-style trajectory admission R0.1

## Purpose

The module is an offline boundary for normalized joint-position demonstrations
exported by a separately installed LeRobot toolchain. It admits caller-supplied
PUBLIC_SYNTHETIC in-memory mappings and returns detached immutable values with
a deterministic SHA-256 digest.

This is not a LeRobot dataset reader, model loader, trainer, simulator,
calibration system, or action executor. It does not claim that a trajectory is
safe, physically plausible, human-authored, or useful for learning.

## Input contract

Schema identifier: moj um.lerobot-trajectory/1 is not valid; the canonical identifier is
mojum.lerobot-trajectory/1.

The root mapping contains exactly schema, provenance, consent, position_unit,
joint_names and frames. Provenance must be PUBLIC_SYNTHETIC, consent must be
explicit, and the only supported position unit is radians.

Joint names are ordered, unique, bounded strings (1..256 entries). The frame
list contains 1..10,000 entries. Each frame has exactly timestamp_ns and
positions_rad. Timestamps are exact non-negative integers, strictly
increasing, and fit a signed 63-bit positive range. Each position vector has
exactly one finite native integer/float per joint, with bounded magnitude.
Booleans are rejected even though Python treats them as integers.

At most 256,000 scalar samples are admitted. Time origin is not interpreted;
duration is last timestamp minus first. Joint order is semantically significant
and preserved in both result and digest.

## Output and digest

The result contains frame/joint counts, duration, timestamps, normalized
positions, ordered joint names and SHA-256. Numeric values normalize to
Python floats; negative zero normalizes to positive zero. The digest covers
canonical sorted-key compact JSON of the normalized trajectory. It is
consistency metadata, not authentication, provenance verification or a
signature.

The frozen result contains tuples only. Caller-owned input containers are not
retained.

## Isolation and limits

- Python standard library only; no mandatory dependencies.
- No filesystem, network, LeRobot package, model weights, robot SDK, SQLite,
  Aquarium, Graph IR, PRIVATE Noosphere or device access.
- Consent/provenance strings are caller assertions, not cryptographic proof.
- No velocities, torques, images, language annotations, episode metadata or
  coordinate transforms are accepted.
- Passing means only that data satisfies this bounded syntactic contract. It
  does not authorize actuation or replace dataset, licensing, privacy, safety
  or calibration review.

Run focused tests from the repository root:
PYTHONPATH=src python -m unittest discover -s tests -p test_lerobot_trajectory.py -v
