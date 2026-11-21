# R0.15 opt-in epoch-v4 foreground host

The existing `python -m mojum.portable_host` behavior stays unchanged: its default is the bounded `developmental_fixture + toy_probe + range_guided` synthetic sensorimotor host.

To run a **new** PRIVATE, CPU-first v4 journal without a toy sensor:

```sh
PYTHONPATH=src python -m mojum.portable_host --db ./private-v4.sqlite3 --ticks 100 --interval-ms 20 --neural-mode disabled --motor-mode local_cell_v4 --sensory-mode none --temporal-mode none
```

Repeat the command to cold-replay and continue the same journal. Each foreground session remains at most 1,000 ticks. The host preflights the Aquarium's versioned event budget, including a pending 9,999-tick epoch transition marker, *before* entering the pacing loop. Invalid motor/sensory/temporal/brain combinations fail before SQLite is created. Journal content stays PRIVATE; host stdout contains metadata, not event payloads.

This is the opt-in **synthetic** foreground controller only, not Android background liveness, neural cognition, HMAC-authenticated replay, physical motor safety, or unlimited epochs. It is a child QA integration on #365, not a second competing v4 persisted ABI.
