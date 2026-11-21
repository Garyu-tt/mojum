# Mojum R0.10 — synthetic object-memory stress QA (negative controls)

**Status:** fixed deterministic *synthetic* regression suite, developed after
the R0.10 algorithm. "Held-out" means these are independent scenarios in
a separate evaluator, **not** an unbiased external generalization dataset.
No real camera, human subjects, neural network, actual object recognition,
statistical confidence interval, independent deployment or validated
real-world localization is involved.

The purpose is to characterize **when an untrained local hypothesis tracker
gets fooled** rather than conceal its shortcomings behind a successful
stationary two-toy demonstration.

## Reproduce

Run in a checkout of PR #34 or a compatible descendant:

```bash
PYTHONPATH=src python -m examples.object_continuity_heldout
PYTHONPATH=src python -m unittest discover -s tests -v
```

The default CLI result is a JSON object containing **only aggregate
synthetic counts**, not toy IDs, source geometry, raw agent memories,
source PRIVATE Noosphere events or individual timestamps. For fully
inspectable synthetic **oracle-only** case traces, explicitly invoke:

```bash
PYTHONPATH=src python -m examples.object_continuity_heldout --with-qa-traces
```

This detailed debug stream is designed for development. Its true labels
`oracle.a` / `oracle.b` and fixture coordinates belong **only to the
external scorer**, never the tracked organism's sensor, association model,
decision loop, or persisted brain/memory. The evaluator explicitly builds
the sensor and numeric self-position, calls
`ProvisionalObjectMemory.observe()`, **then** looks up the true label
to count a match, false merge or split.

## Cases and measured interpretation

| Fixed scenario | What we check | R0.10 finding |
|---|---|---|
| Two static same-RGB toys, 12 samples | Can a location cue separate objects identical in RGB? | Two hypotheses, 10 repeat matches, **0 identity switches** |
| Temporary mask of near-field signal, 9 samples | Can a former hypothesis survive 3 missing signals? | One hypothesis; one tentative `possible_reencounter` |
| Small +/-1 numeric RGB perturbations, 12 samples | Is exact RGB hashing robust to small noise? | **No.** A single real object fragments across multiple hypotheses |
| One moving toy, 17 consecutive observations | Does a stationary landmark estimate track moving objects? | **No.** The same toy fragments into separate hypotheses |
| Two identical-RGB toys swap physical positions, 4 samples | Does spatial continuity imply actual object identity? | **No. Two wrong associations**, both including a wrong attribution of measured touch |
| Same crossing with optional measured-contact consistency guard, 4 samples | Can observed contact disagreement stop overwriting individual track memory? | **0 wrong track associations, 0 contaminated track contacts; 2 explicit abstentions** |

These are deterministic fixture counts/qualitative results, **not**
population-level percentages. The test logs print the compact JSON
baseline with an `MOJUM_OBJECT_QA_COMPACT=` prefix so subsequent
revisions can compare against a reproducible result.

## Error accounting

- **matched_reencounters:** a reported hypothesis has been mapped by
  the external oracle to the same toy at an earlier observation.
- **identity_switches:** that hypothesis is matched to a **different**
  oracle toy (false merge/association).
- **splits:** a new hypothesis is assigned to an oracle toy that already
  owns at least one earlier hypothesis (false splitting of an object).
- **ambiguous_abstentions:** the tracker explicitly refused to assign
  a single hypothesis because more than one local candidate was eligible.
- **contact_conflict_abstentions:** with the optional guard enabled, a
  measured contact deflection differed by more than 0.30 from the
  single candidate track's previous empirical mean. The tracker kept
  the encounter unknown rather than modify that track. Without a
  probe measurement, this guard has no evidence to work with.
- **contaminated_contacts:** measured contact feedback is attributed to
  a hypothesis whose earlier oracle association belongs to a different
  toy. This is a concrete **memory poisoning via ambiguity**, despite
  no attacker or adversarial input in the scenario.
- **possible_reencounters:** an association after a gap in observations;
  this is a tentative operational label, **not** verified identity.

Some synthetic false merges are invisible to the organism's current
RGB/range/azimuth/self-position observations. In particular, the
crossing example is not resolvable from the currently available sensory
channels without additional information or uncertainty-aware constraints.

## Engineering consequences

Do **not** claim R0.10 has learned reliable object permanence or robust
visual identity. It can show repeatable hypotheses in simple stationary
scenes, but it demonstrably merges/swaps identities under crossing and
splits single items under noise/motion. Never infer personality, social
identity or interpersonal trust from these uncertain object labels.

The initial controlled comparison now includes a **fail-closed,
opt-in measured-contact consistency guard**. In the crossing toy fixture,
the unguarded tracker makes two false associations and contaminates
two provisional contact profiles; the guarded tracker instead abstains
twice, so those two profiles retain their earlier observed responses.
This is not a general identity solution, and the global R0.9
**RGB-only association** still records raw contact measurements and can
blend distinct same-RGB objects: the guard protects the *per-hypothesis
contact model*, not every form of memory.

The next controlled work should test sensor-noise tolerant
representations and a temporal motion model on independent new fixtures.
A safer algorithm may abstain more often rather than confidently
assign the wrong toy. Track both failure and abstention so apparent
accuracy cannot be inflated by hiding hard cases.

A CI-pass means the benchmark and its negative findings are
reproducible; it does **not** mean the current tracker passes a robust
object-recognition gate. The PR still needs independent code review.
