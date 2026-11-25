# Aquarium human-declared identity CLI R0.1

The installed package can inspect or assign the existing human-declared name
of a committed Aquarium journal. Use `python -m mojum.aquarium_identity` from
any working directory after installing the wheel. Repository users can set
`PYTHONPATH=src` before the same commands.

## Inspect and assign

Create an ordinary Aquarium journal first, then inspect its current name and
head:

```sh
python -m mojum.aquarium --db organism.sqlite3 --ticks 2
python -m mojum.aquarium_identity status --db organism.sqlite3
```

The JSON receipt reports `display_name: null` and
`name_provenance: "not_assigned"` for an unnamed organism. Copy its
`journal_head` into the explicit assignment request:

```sh
python -m mojum.aquarium_identity assign --db organism.sqlite3 \
  --display-name "Данте" --actor-id local-owner \
  --expected-head "REPLACE_WITH_THE_64_CHARACTER_JOURNAL_HEAD"
```

The name comes from the caller. The command appends exactly one canonical
`identity.assigned` event through `Aquarium.assign_human_name()`. It preserves
physics ticks, virtual time, developmental age, genome and learned state.
Ordinary Aquarium restart replays the name together with previous frames;
later simulation commands continue the same organism.

Names may contain valid Unicode and must satisfy the existing 80-character
identity contract. The actor identifier is bounded to 128 characters. Empty
or whitespace-only text, control/invisible characters and invalid UTF-8 are
rejected. A second assignment is refused: renaming and automatic name
generation are unsupported.

The actor and `assigned_by: "human"` are declarations, not authentication.
The existing `human_declared_unverified` provenance remains explicit. Use
trusted local storage and authenticated access control around any service
that exposes this operation to other people.

## Match the journal's runtime configuration

Both commands accept the original `--genome`, `--graph`, `--room-file`,
`--neural-mode`, `--motor-mode`, `--brain-mode`, `--sensory-mode`,
`--temporal-mode`, `--pulse-sum` and `--dt-ns` selectors. Packaged genome and
graph defaults work without a checkout. The journal owner is the supplied
genome's `genome_id`; it is not inferred or overridden.

Use the same profile that created the journal. For example, a developmental
controller journal needs both `--motor-mode developmental_fixture` and
`--brain-mode developmental_controller`. Configuration mismatches fail during
read-only replay. Custom JSON inputs are limited to 256 KiB each; duplicate
keys, noncanonical constants, excessive nesting and nonregular input files
are rejected before journal access.

`--sensory-scene default` uses the canonical default toy scene. An explicitly
empty scene is available as `--sensory-scene empty` with `toy_probe` or
`toy_probe_v2`; its dimensions match the supplied room. Arbitrary custom toy
layouts still require the Python API. Room files pin their original start;
an independently customized start cannot be expressed without a room draft.

Read-only `status` supports the existing replay profiles, including archived
local-cell journals. Assignment supports `scripted` and
`developmental_fixture` journals. Local-cell formats allow only their existing
frame/epoch topics and therefore reject naming before writable access.

## Admission, concurrency and receipts

Both actions require an existing regular journal and perform complete
read-only deterministic replay. They do not initialize a missing, empty or
foreign database. `status --expected-head SHA256` optionally checks an
independently retained anchor. Assignment requires that anchor, verifies
available event budget and the absence of a previous name, then reopens with
the same required head before calling the canonical append operation.

A competing writer makes an old anchor stale. Inspect the current journal
and decide whether to submit a new request. The two opens are not an atomic
filesystem transaction; trusted local parent directories remain an
assumption. SQLite can create auxiliary files while reading an existing WAL
journal, so read-only inspection is not a byte-for-byte filesystem snapshot
guarantee. The operation never deliberately opens a rejected source writable
during its initial admission.

Receipts use `mojum.aquarium.identity-cli/1` with `action`, `agent_id`, `tick`,
`sim_time_ns`, `development_months`, `stored_events`, `journal_head`,
`display_name`, `name_provenance` and `trust`. Counts and heads refer to the
verified or committed revision. JSON escapes Unicode so names remain
recoverable through ASCII and CP1251 terminals. The receipt exposes identity
metadata; journal frames, genome contents and learner state remain in the
PRIVATE journal.

The stored identity and journal schemas are unchanged. This CLI operates on
the existing synthetic fixture and provides no evidence of cognition,
biological identity or authenticated human authorship.

## Regression checks

```sh
PYTHONPATH=src python -m unittest tests.test_aquarium_identity_cli \
  tests.test_development_journal tests.test_development_cas_integration \
  tests.test_development_committed_head -v
```

Release verification also builds and installs the wheel in a clean virtual
environment, invokes the module outside the checkout, inspects and names a
real journal, then reopens it through the installed Aquarium runtime.
