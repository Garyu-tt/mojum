# Bounded offline CPU host

`mojum.portable_host` runs the current canonical synthetic Aquarium in a
bounded foreground process. Each tick is chosen by the opt-in developmental
motor controller, optionally guided by previously measured numeric range
outcomes, and committed to the same PRIVATE SQLite journal used by the
direct Aquarium CLI.

This is a local CPU/std-lib fixture. It is not a neural brain, a physical
actuator, MuJoCo, an Android service, encrypted memory, distributed failover,
or a 24/7 execution guarantee.

## Run locally

From the checkout:

```text
PYTHONPATH=src python -m mojum.portable_host --db ./dante-host.sqlite3 --ticks 20 --interval-ms 50
```

For a new journal, use `--brain-mode developmental_controller` to select the
explicit synthetic controller. The same option is required for restart;
`placeholder` remains the default for existing compatible journals.

An installed package also exposes `mojum-host`. Re-run the same command with
the same database to resume at the committed tick instead of creating a
second organism. Stdout contains metadata only: tick range, journal head,
event count, pacing and an explicit synthetic classification; private frames,
genome values, sensor measurements and learner state are not printed.

The host accepts 1..1000 ticks and a 20..3,600,000 ms wall-clock interval.
The interval never changes Aquarium's virtual `dt_ns`. If the process wakes
late, it does not create catch-up ticks. Ctrl-C or POSIX TERM stops between
commits; a completed SQLite append is replayed on the next invocation.

The default `developmental_fixture` profile requires a toy sensory bridge and
uses its fixed motor-play budget. The opt-in `local_cell_v4` profile accepts
either sensorless (`--sensory-mode none`) or versioned numeric sensory
(`--sensory-mode toy_probe_v2`) operation, always with
`--temporal-mode none`. It uses the versioned epoch marker at tick 9,999 and
preflights both frames and markers against the 100,000-event journal ceiling.
The legacy `toy_probe` selector is rejected for v4. A rejected plan never
appends a new frame. The journal checksum is integrity evidence, not
authentication.

## Verification

```text
PYTHONPATH=src python -m unittest -q tests.test_portable_host
```

The tests cover deterministic pacing, early wakeups, stop-before-next-tick,
budget preflight, real CLI restart/replay, private stdout, mode/head fencing,
and POSIX signal interruption where supported.
