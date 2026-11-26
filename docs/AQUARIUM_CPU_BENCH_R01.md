# Mojum Aquarium CPU benchmark R0.1 (opt-in)

Parent: [#11](https://github.com/Garyu-tt/mojum/issues/11). This is a
**resource characterization tool**, not an organism release or a performance
guarantee. It tests the existing stdlib CPU-first synthetic Aquarium, not
MuJoCo, a trained brain, mesh failover or PRIVATE user data.

## Reproduce

From a checkout of this branch on Python 3.11, 3.12 or 3.13:

```sh
PYTHONPATH=src python tools/aquarium_cpu_benchmark.py --ticks 64 --repetitions 3 --timeout-sec 60
PYTHONPATH=src python -m unittest discover -s tests -p 'test_aquarium_cpu_benchmark.py' -v
```

On Windows PowerShell, set `$env:PYTHONPATH = "src"` before the same commands.
No third-party packages, network service, Python wheel installation, existing
journal or credentials are needed.

The command launches a fresh `python -m mojum.aquarium` child for each
**cold** phase, with explicit `--neural-mode disabled --motor-mode
developmental_fixture --sensory-mode toy_probe`. In a *second distinct child
process*, **restart** reopens the same generated SQLite journal and passes its
independently captured preceding `journal_head` back as `--expected-head`.
This fails closed on mismatched identity/head before further appends.

Each repetition gets a new `TemporaryDirectory` and
`synthetic_PRIVATE.sqlite3`. The benchmark accepts the canonical event
count `1 + ticks` for cold and `1 + 2*ticks` for restart, verifies strict
integer ticks, strict lowercase 64-hex journal heads, and requires the head to
advance after restart. Child stderr is intentionally not echoed. The entire
temporary tree is deleted on normal success or failure.

Only aggregate timing data and the following non-secret descriptors enter the
JSON report:

- `schema = mojum.benchmark.aquarium-cpu/1` and
  `fixture = PUBLIC_SYNTHETIC_DISPOSABLE`
- Per-repetition `cold.wall_ns` and `restart.wall_ns`, measured across
  spawn, fixture execution, SQLite closure, and JSON output
- Per-phase `child_cpu_ns`, taken from the POSIX child `resource` counter;
  `null` when not supported (notably Windows)
- Integer median wall and child CPU nanoseconds for the two phases

No journal bytes, source hash heads, filesystem paths, device identity,
PRIVATE Noosphere payloads, env vars, tokens or source records are printed.
The original report schema is versioned; a future extension must use a new
schema when meaning changes.

## Resource / failure bounds

`1 <= --ticks <= 1000`, `1 <= --repetitions <= 12`, and
`1 <= --timeout-sec <= 120` are enforced **before** any subprocess.
Each child has its own timeout. The captured JSON output is rejected after
capture if over 128 KiB; this is an **admission bound, not an OS pipe-memory
or CPU hard quota**. A child timeout raises a benchmark failure rather than
reporting a partial success. JSON is parsed with duplicate-key and nonfinite
constant rejection, including nested objects. A nonzero exit, invalid
tick/count/head, stalled journal head, bad child CPU reading or non-monotonic
wall clock aborts the run.

## Interpretation

The "cold" measurement includes first-time SQLite/bootstrap cost. The
"restart" includes authenticated replay of a longer synthetic journal. It is
*not* a pure 1-tick latency measurement and must not be compared with #893's
offline tick-duration histogram. OS page cache, antivirus, thermal throttling,
disk filesystem and Python version alter results. Use multiple repetitions
on the *same machine* and compare distributions, not one noisy number.

The test suite exercises malformed/error paths through fake child runners **and**
executes a two-process real SQLite cold/restart smoke on a disposable one-tick
PUBLIC_SYNTHETIC fixture (never a user journal). It asserts no fixed timing
threshold beyond a positive observed wall duration. Running the CLI command
above additionally produces multi-sample operator measurements. Independent
exact-head GitHub CI/QA remains necessary before any merge. No existing runtime, Noosphere, Graph IR, or acceptance gate
is modified by this change.
