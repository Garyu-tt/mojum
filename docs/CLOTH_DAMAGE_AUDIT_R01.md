# Cloth damage threshold audit R0.1

## Purpose

`mojum.cloth.damage-audit/1` is an opt-in, offline, read-only geometric audit over caller-supplied `PUBLIC_SYNTHETIC` edge stretch ratios. It classifies each sample against an explicit caller-provided threshold and emits a deterministic SHA-256 consistency digest.

## Input

A native Python mapping with exactly these fields:

- `schema`: `mojum.cloth.damage-audit/1`
- `provenance`: `PUBLIC_SYNTHETIC`
- `consent`: `true`
- `profile_revision`: bounded revision token
- `threshold`: finite stretch ratio in (1, 100]
- `samples`: at most 4096 ordered samples, each containing `tick`, `edge_id` and `stretch_ratio`

Ticks are nondecreasing and an edge may appear at most once per tick. Numeric values require native int/float types; booleans and non-finite values are rejected.

## Output and limits

The immutable report contains each detached classification, sample and exceedance counts, the pinned profile revision, and a deterministic SHA-256 digest over canonical report content. The digest is a consistency witness, not a signature or authentication mechanism.

## Safety boundary

This module does not calculate FEM stress, infer force/tension, simulate cloth, create tear geometry, detect collisions, load assets, access files/network/SQLite/Noosphere, or control motors. A threshold exceedance is an advisory geometric condition only. No material calibration or physical damage claim is implied.
