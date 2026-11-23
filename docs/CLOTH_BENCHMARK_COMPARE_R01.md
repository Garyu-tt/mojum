# Offline cloth benchmark comparison R0.1

This optional tool normalizes caller-supplied PUBLIC_SYNTHETIC measurements
from separate cloth-solver experiments. It does not run a physics engine.

## Input and bounds

UTF-8 JSON schema: `mojum.cloth-benchmark-report/1`, visibility `PUBLIC_SYNTHETIC`, 2–16 run records.
Each record has engine/version, scenario id, seed, mesh SHA-256, vertex and
constraint counts, timestep, total/warmup steps, one CPU microsecond sample per
post-warmup step, peak RSS bytes, penetration count and dropped-step count.

Input is limited to 1 MiB, 1,000 samples per run and 8,000 samples total.
Unknown/duplicate keys, bool-as-int, non-finite values, mismatched sample count,
invalid digest, private visibility, symlink input and output overwrite are
rejected. The implementation uses Python standard library only.

## Run

    python tools/cloth_benchmark_compare.py report.json
    python tools/cloth_benchmark_compare.py report.json --output comparison.json

Median and p95 use the nearest-rank definition. Candidates are ranked only if
all workload identity fields match exactly. Otherwise each candidate is shown
without a ranking. Outcome counters remain visible and are not folded into a
weighted score.

## Interpretation limits

Caller-supplied penetration and dropped-step counts are not independently
verified against trajectories. Even matching scenario identifiers do not
prove equal material, contact, friction or convergence semantics. Timing
depends on machine, build, thread count, affinity, thermal state and load.
An ordering is not a solver winner, physics-equivalence proof or performance
guarantee. No MuJoCo, Jolt or PBD dependency, install, import or execution is
performed. No production runtime or Aquarium behavior is changed.
