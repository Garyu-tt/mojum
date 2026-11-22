# PUBLIC-only Noosphere index staging (R0.1)

Issue #692; related parent #8. This is a pure CPU/stdlib **pre-index projection helper**, not an actual sqlite-vec installation, embedding model, private indexing service or authenticated memory export.

`stage_public_index` consumes only the exact existing `project_episodes` data shape (which excludes PRIVATE and RESTRICTED events by default). It checks the hash of the canonical projected payload, validates field shape/budgets and derives stable keys for later offline vectorization.

```python
from mojum.dataset_view import project_episodes
from mojum.public_index_staging import stage_public_index

visible = project_episodes(events, allowed_sources={"explicitly-authorized-source"})
rows = stage_public_index(visible)
```

**Security boundary:** a SHA-256 matches data but does not authenticate it. A forged PUBLIC view may carry actually-private content; callers must obtain the dataset exclusively from an authorized, trusted Noosphere projection and verify source identity/authorization independently before index use. Never treat this helper as a capability grant. No database is opened or created, and no external service is contacted.

Next acceptance slice: optional local sqlite-vec extension loaded from an explicitly trusted path; index per organism, visibility, dataset SHA and embedding version. Verify revoked source visibility removes its derived rows; test corrupt index rebuild without touching canonical journals, and independently verify privacy boundaries.

Run: `PYTHONPATH=src python -m unittest tests.test_public_index_staging -v`.
