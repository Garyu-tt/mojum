# School curriculum transfer audit R0.1

## Purpose

`mojum.school_transfer_audit.audit_catalog` inspects a caller-supplied
`mojum.school-catalog/1` object or UTF-8 JSON byte string and returns a
deterministic structural report. It considers section-level
`cross_subject_ids` and optional lesson-level `transfer_to` declarations.

This is an offline, read-only utility. It does not edit the curriculum, run a
lesson, record a learner event, assess skill, or prove that transfer occurred.

## Use

```python
from pathlib import Path
from mojum.school_transfer_audit import audit_catalog

report = audit_catalog(Path("examples/school_catalog_r01.json").read_bytes())
print(report.as_dict())
```

The caller controls how input bytes are obtained. The module itself does not
open files, access a network, install dependencies, or write artifacts.

## Findings

- `UNKNOWN_CROSS_SUBJECT_TARGET`: section reference has no matching subject.
- `SELF_CROSS_SUBJECT_LINK`: section references itself.
- `DUPLICATE_CROSS_SUBJECT_LINK`: repeated section reference.
- `MISSING_REVERSE_CROSS_SUBJECT_LINK`: one-way section relation. This is an
  advisory consistency finding, not an error: subject associations need not
  be symmetric in all future catalog designs.
- `UNKNOWN_LESSON_TRANSFER_TARGET`: lesson declares a nonexistent subject.
- `SELF_LESSON_TRANSFER`: lesson transfers to its own subject.
- `DUPLICATE_LESSON_TRANSFER`: repeated lesson target.
- `DUPLICATE_LESSON_ID`: lesson identity is repeated across the catalog.

Lesson transfer declarations are directed. The auditor deliberately does not
require a reverse lesson declaration.

## Limits and privacy

The parser rejects duplicate JSON keys, non-native Python containers, cycles,
non-finite numbers, unknown Python objects, excessive depth, excessive node
count, oversized catalogs, invalid section/lesson collections, and excessive
reference counts. The output contains identifiers, finding codes and hashes
only; it does not echo lesson text, prompts, learner names or artifacts.

The SHA-256 values are reproducibility checksums, not signatures, provenance
proofs or authentication. The utility is CPU/stdlib-only and has no runtime,
SQLite, Noosphere, Graph IR, scheduler, or actuator integration.

## Verification

Run:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_transfer_audit.py' -v
```

Exact-head GitHub CI and independent QA are required before merge.
