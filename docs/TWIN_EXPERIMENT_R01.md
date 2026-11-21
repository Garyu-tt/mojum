# Offline synthetic twin experiment R0.1 — Draft

This is a **CPU-only test fixture**, not two conscious children, two trained
personalities, or autonomous goal-free development. The canonical Aquarium
brain remains `UNIMPLEMENTED`; shadow pulse prediction does not select actions.
There is no food/water survival requirement, pain, affect or age-related death.

The two mentors provide **distinct, human-chosen IDs**. The `pair` command
copies the same inherited GENESIS traits and template revision but assigns
a separate `genome_id` and independent PRIVATE SQLite journal per actor.
The shared-heritable fingerprint deliberately excludes actor identity.
This does not mean the entire persisted genomes are byte-identical.

From the repository root:

```sh
PYTHONPATH=src python -m mojum.twin_lab pair \
  --left-db ./twin_a.sqlite3 --right-db ./twin_b.sqlite3 \
  --left-id mentor-a-chosen --right-id mentor-b-chosen

PYTHONPATH=src python -m mojum.twin_lab mentor \
  --db ./twin_a.sqlite3 --agent-id mentor-a-chosen --ticks 8 --dx 0.05

PYTHONPATH=src python -m mojum.twin_lab mentor \
  --db ./twin_b.sqlite3 --agent-id mentor-b-chosen --ticks 4 --dx -0.05
```

The `mentor` command operates on only one journal, cold-replays its entire
history and refuses a missing file, a wrong owner ID, bad displacements, or
event-budget overflow. The original `Aquarium` remains the storage authority.

**Important limits:** movement comes from the mentor's scripted external
input and resulting position divergence does not prove learned development.
No sensory meeting events, transfer of knowledge, encrypted backup, observer
UI, consent protocol or true cross-database transaction exists yet.
`pair` now reserves both new SQLite names using exclusive `O_CREAT|O_EXCL`
creation before opening Aquarium, so a second creator cannot silently replace
an existing path after the initial check. If the second reservation fails, the
first empty reservation is removed when its identity still matches. This
requires a trusted, private parent directory; it does not lock out malicious
same-UID processes, provide durable two-file commits, or prevent hostile
filesystem replacement after reservation.

A crash between the two births can leave only one journal: inspect both files,
never retry `pair` over existing paths. The SQLite files are PRIVATE and
must not be published. Their hash chains are not signatures.

Regression: `PYTHONPATH=src python -m unittest discover -s tests -v`.
