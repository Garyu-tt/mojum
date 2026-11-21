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

New journals may opt into the explicit synthetic brain boundary with
`--brain-mode developmental_controller`. Resume that journal with the same
flag; the default remains `placeholder`, and mode mismatches fail closed
before adding events. This controller is deterministic and synthetic, not
a trained neural brain.

An installed package also exposes `mojum-host`. Re-run the same command with
the same database to resume at the committed tick instead of creating a
second organism. Stdout contains metadata only: tick range, journal head,
event count, pacing and an explicit synthetic classification; private frames,
genome values, sensor measurements and learner state are not printed.

The host accepts 1..1000 ticks and a 20..3,600,000 ms wall-clock interval.
The interval never changes Aquarium's virtual `dt_ns`. If the process wakes
late, it does not create catch-up ticks. Ctrl-C or POSIX TERM stops between
commits; a completed SQLite append is replayed on the next invocation.

The host refuses a run that would exceed Aquarium's fixed 10,000-event replay
budget, or that changes the persisted temporal mode / expected journal head.
Those failures happen without appending a new frame. The journal checksum is
integrity evidence, not authentication.

## Verification

```text
PYTHONPATH=src python -m unittest -q tests.test_portable_host
```

The tests cover deterministic pacing, early wakeups, stop-before-next-tick,
budget preflight, real CLI restart/replay, private stdout, mode/head fencing,
and POSIX signal interruption where supported.
