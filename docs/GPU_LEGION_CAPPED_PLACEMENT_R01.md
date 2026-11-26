# Replica-capped exact GPU placement

The read-only `tools/gpu_legion_plan.py` planner optimizes the actual requested
plan for up to 12 supported GPUs. Its objective is, in order:

1. Maximize disjoint model replicas up to `--max-replicas`.
2. Minimize GPU card count for those replicas.
3. Minimize excess usable VRAM.
4. Break equal scores deterministically by GPU-index groups.

The replica cap participates in the search rather than truncating a larger
layout. For usable VRAM `[2, 2, 4]` GiB and a 4 GiB model, a cap of one uses
the single 4 GiB GPU and leaves both smaller cards available. A cap of two
uses both the `[0, 1]` pair and GPU `[2]`.

The exact search caches device masks plus remaining replica slots, bounded
by 12 cards and at most 12 useful slots. Larger fleets retain the existing
explicitly labeled `bounded_greedy` estimate. Public `max_replicas` admission
remains integer 1–32; it does not accept zero, booleans or `None`.

Tests compare 1,296 small-inventory plans to an independent exhaustive
enumeration of feasible disjoint group sets. Regression cases cover card
count, VRAM slack, sparse indices, shuffled inventories, twelve/thirteen-card
boundaries, CLI shortfall status and real POSIX inventory-file execution.
Windows tests exercise the portable optimizer and CLI with supplied probe
data; secure inventory-file reading remains POSIX-only.

This remains a capacity estimate. It does not load models, benchmark CUDA,
verify compatibility, start services or authorize deployment.
