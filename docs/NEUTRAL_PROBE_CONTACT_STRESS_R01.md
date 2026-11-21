# Mojum R0.14 — contact-rich development stress R0.1

This is a **deliberately selected stress test**, not a blind, independently
held-out generalization experiment. The previous ten-case suite, frozen at
`b46a6a156cb250552b8069affc69703e8c48d1643eefe8eddbf5e6d80850283c`,
had only 3/10 scenarios with any frozen-policy collisions. Its failure
motivated this *new*, clearly labeled set. Do not retroactively combine
its evidence with the previous suite or assert independent validation.

## Design

Twelve fixed synthetic small rooms and corner/border starting positions,
including two central interior obstacles and four independent deterministic
pulse schedules: alternating, constant off, bursts and seeded. Sixteen
scenarios maximum, 160–192 ticks per case, one device, no GPU/network/SQLite.
The only policy observation is the same typed current proprioception and
the same synthetic pulse stream. The world, not the learner, computes the
actual next movement blocked/unblocked result.

Arms: canonical half-meter Explorer, no-learning innate frozen control,
research 4-cm-cell outcome inhibition. Each starts independently at the
same position and receives the same pulse stream. Their positions diverge
if they act differently; this is valid causal behavior, not a shared
trajectory replay.

The runner fails the **contact-exposure QA gate** unless the FROZEN arm
actually collides at least once in >=10 of 12 cases. This is a test of the
*evaluation fixtures*, NOT that learning succeeded. Report separately:
blocked attempts, successful moves, rest ticks, idle streak, unique observed
positions, collisions / attempted moves, successful moves / total ticks,
stalled scenarios and wins/equalities/losses of the local-cell arm.
Never claim "less blocked = better" when the organism simply stopped.

```bash
PYTHONPATH=src python -m mojum.neutral_probe_contact_stress
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neutral_probe*.py' -v
```

The public CLI reveals aggregate numeric metrics plus provenance hashes,
not per-tick traces, private logs, users, devices or learned state.

## Release interpretation

High contact exposure improves **test sensitivity** but is intentionally
manufactured after seeing previous results. Even passing all stress tests
cannot establish general-purpose navigation, sentience/absence of suffering,
safe physical actuation, or that this local-cell policy improves every scene.
An external independent and untuned evaluation is still required before
adopting a versioned policy for canonical Mojum. No change is made to the
R0.13 runtime, existing historical journal policy schema or checkpoints.
