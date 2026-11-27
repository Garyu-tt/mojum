# GENESIS ledger pilot

Standalone extraction from legacy 074b14c152c8c5e3291e6e56595255386a751af9. The Python implementation, two unit test files, seed, license and existing contract are exact legacy blobs. No dependencies outside Python stdlib. Run `PYTHONPATH=src python -m unittest discover -s tests -v`.

This is not the whole GENESIS system. Prefix sweep imports Aquarium and remains in legacy for integration regression; timeline, lifecycle and body integration are out of scope. No core SDK compatibility claimed until it exists. Do not merge main here. Release acceptance and protected branch setup pending.
