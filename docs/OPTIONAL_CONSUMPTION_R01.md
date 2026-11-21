# Optional consumption R0.1: sensory play without survival dependency

An **isolated, opt-in, Python-stdlib synthetic fixture** for recording food tasting
and water sipping as sensory experiences. This module is not wired into the
canonical Aquarium, GENESIS, Noosphere, neural policy, physics or real actuation.

## Safety and behavior contract

- Food and water are **never required** for organism existence, continuity,
  development, or learning. There are no hunger/thirst counters, starvation,
  dehydration, malnutrition, aging penalties, death, or forced feeding.
- On *every* tick, even after arbitrarily long non-consumption, frame()
  returns food_required_for_survival=false,
  water_required_for_survival=false, and deprivation_effect=null.
- An event requires opt_in=true, but this is an **assertion by its source**,
  not proof of autonomous choice or verified consent. The source is marked
  scripted_fixture or measured_adapter. Independent agent choice is out of scope.
- No calories, hydration, survival rewards/penalties or physical actuation may
  be added to this event schema; unknown fields are rejected.
- Sensory tags are categorical simulated descriptors, **not** physiological
  measurements. Future adapters may offer experience for learning but must
  keep positive sensory play independent from survival.
- This is a PRIVATE, in-memory diary: no automatic publication, transport,
  persistence, encryption, or network. Do not upload real user interaction data.

## Wire formats

Event: mojum.sensory.optional-consumption-event/1. Exact fields:
schema, event_id, organism_id, tick, kind, stimulus_id, flavor,
temperature, texture, opt_in, source.

Kinds: food_taste and water_sip. A water_sip event requires stimulus_id=water
and texture=liquid. IDs are bounded ASCII tokens, ticks are nonnegative
64-bit integers. Categorical sensory values and source are strictly validated.

Ledger snapshot: mojum.sensory.optional-consumption-ledger/1. Contains
organism_id, events, and revision. Append uses expected-revision CAS,
strict nondecreasing ticks, unique event IDs and a 512-event cap.
Snapshots replay with the same rules. SHA-256 is **an integrity check only**;
it is not authenticated, signed, anti-rollback, or a substitute for Noosphere.

Frame: mojum.sensory.optional-consumption-frame/1. Includes exact per-tick
experiences plus constant non-deprivation invariants. Projection does not
change state. No state or survival effect is inferred from missing events.

## Verify

From the repository root:

    PYTHONPATH=src python -m unittest discover -s tests -p test_optional_consumption.py -v

Follow-up integration needs separate privacy/consent review, an explicit
versioned journal bridge, and cold-replay acceptance tests showing that
non-consumption never changes continuity or survival.
