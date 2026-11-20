# Start synthetic Mojum aquarium

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120
PYTHONPATH=src python -m mojum.aquarium --db ./dante_fixture.sqlite3 --ticks 120
```

On first invocation the journal is created and populated. The second invocation resumes; no remote services or GPU needed. The brain is an explicit placeholder, the room is a point-fixture, and there is **no MuJoCo**. Do not store real personal memories in the unencrypted SQLite file. See `docs/AQUARIUM_R02.md`.
