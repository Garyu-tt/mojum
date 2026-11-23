# QA dependency DAG evaluator R0.1

## Purpose

`mojum.qa-dependency-dag/1` is a bounded, deterministic admission utility for caller-supplied PR dependency metadata. It supplies a dependency-first ordering and classifies open work as READY or BLOCKED. It is a pure planning primitive for the persistent QA supervisor, not a scheduler or merge gate.

## Input contract

Pass only a native Python `list` or `tuple` of records. Every record must be an exact built-in `dict`; `dict`/`list`/`tuple` subclasses and custom `Mapping`/`Sequence` implementations are rejected before traversal. All record keys and identifier/state values must be exact built-in `str` objects. Each record has exactly these fields:

- `id`: non-empty PR identifier, at most 128 characters.
- `state`: `OPEN`, `MERGED`, or `CLOSED`.
- `depends_on`: exact built-in `list` or `tuple` of distinct PR identifiers that must be merged first.

All referenced identifiers must exist in the same input. Self-edges, duplicate nodes/edges, dangling references, unsupported states and cycles fail closed with `ValueError`.

Global limits are 500 nodes and 4,000 edges. Callers may lower, but cannot raise, these limits.

## Output semantics

- `topological_order` is stable and dependency-first. Lexicographic ordering breaks ties.
- `MERGED` and `CLOSED` records are terminal.
- An `OPEN` record is `READY` only when every direct dependency is `MERGED`.
- Otherwise an open record is `BLOCKED` and lists the non-merged dependencies in `waiting_on`.
- A closed dependency does not satisfy a dependency; its child remains blocked.

## Safety and limitations

This module consumes only explicitly supplied metadata. It performs no GitHub API calls, network access, SQLite writes, private Noosphere reads, subprocess execution, model inference, lease management, or merge authorization. It does not verify that a caller's PR states or dependency edges are truthful or current. The caller must refresh source metadata and independently enforce exact-head CI, branch protection, authorization, freshness, and release policy before any merge.

The evaluator is Python standard-library-only and does not alter existing Aquarium, graph, motor, memory, or journal schemas.
