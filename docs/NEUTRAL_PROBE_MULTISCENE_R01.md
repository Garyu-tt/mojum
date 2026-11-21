# R0.14 neutral-probe multi-scene evaluation R0.1

## Boundary

This is an **out-of-initial-fixture synthetic characterization**, not a blinded
independent study. It is stacked on research PR #104, not a change to Dante's
canonical Aquarium, Noosphere history or motor-control contracts. The
predeclared cases are visible in source, so subsequent tuning on them makes
them development benchmarks rather than independent held-out evidence.

There are ten bounded (160–192 tick) scenes with open rooms, obstacles,
narrow passage configurations, border starts and a thin wall. Pulse bitstreams
are deterministic (off, alternating, grouped bursts, seeded integer generator)
and are **identical across the three arms within each case**. All arms start
at the same position. No physical body, GPU, privacy-sensitive recordings,
real-world sensor, pain, reward, valence or user data is involved.

The three compared arms are:

- `adaptive`: existing canonical half-meter zone Explorer (research copy).
- `frozen`: identical innate pulse-rotation, with no learned inhibition.
- `local_cell`: experimental four-centimeter proprioceptive cell inhibition.

For every case the report records contacts, idle ticks, attempted and
successful movements, approximate distinct positions, long idle streaks, stall
flag, and SHA-256 of the *synthetic* trace. Aggregate wins/losses/ties
are **descriptive counts, not an optimization reward**. One arm can
avoid collisions by stopping; report inactivity alongside contacts.

### Exact first-run outcome / methodological blocker

At first successful exact-head CI run
[44850012246](https://github.com/Garyu-tt/mojum/actions/runs/44850012246)
the immutable suite manifest had SHA-256
`b46a6a156cb250552b8069affc69703e8c48d1643eefe8eddbf5e6d80850283c`.
Across 10 cases and 1728 ticks **per arm**, the figures were:

| Arm | Blocked | Rest | Successful moves | Stalled scenes |
| --- | ---: | ---: | ---: | ---: |
| Canonical coarse-zone | 14 | 566 | 1148 | 2 |
| Frozen no-learning | 39 | 345 | 1344 | 0 |
| Research local-cell | 21 | 355 | 1352 | 0 |

Local-cell had fewer blocked attempts than frozen in **2/10** cases and
exactly equal in **8/10**; it had more successful moves in **2/10**
and exactly equal in eight. **Only 2/10 scenarios generated ANY collision
for the frozen arm.** In the other eight, all three arms' trajectories are
identical, so these scenes say almost nothing about learning. This
initial benchmark therefore has **LOW_CONTACT_EXPOSURE**, not evidence
of generalizable learned navigation.

The ten fixtures were frozen after this result. The report now publishes
the contact-scene count and divergence count alongside aggregate totals.
The 50% exposure threshold is a declared heuristic for catching weak test
coverage, **not** a statistical proof or biological criterion.
Any future contact-rich development stress suite must be explicitly labeled
as chosen after seeing this baseline, and an independent reviewer should
provide a truly new evaluation set before release.

## Run

```bash
PYTHONPATH=src python -m mojum.neutral_probe_eval
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neutral_probe*.py' -v
```

The CLI emits only deterministic scene-level summary numbers and hashes, not
whole trajectories, SQLite PRIVATE frames, learned model state or private
Noosphere history. There are no writes, network calls or privileged capabilities.

## Gates before a production neural/motor integration

1. Independently inspect all scene metrics, *including any worse local-cell
   case*; do not cherry-pick lower blocked counts.
2. Measure position coverage and real movement, not only low collisions.
3. Use a future independent fixture or sealed set before claiming
   generalization. Add adversarial rotations, different grid resolution,
   noisy proprioception and prolonged periods without heartbeat changes.
4. If any exploratory controller is adopted, pin an explicit new
   developmental-policy schema, check old saved event journals and replay
   validation, and keep legacy policy code available for old logs.
5. Keep physical actuation prohibited and no new affective distress channels.

This experiment is intentionally review-only. A passing test suite does not
approve a release, grant consciousness, prove suffering impossible or justify
auto-merging to main.
