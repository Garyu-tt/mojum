# Neural proposal-only wire, R0.1

Issue #691; relates #6 and #4. This PR defines a **bounded passive schema**, not a working ANNarchy engine, full GENESIS brain, plasticity, consciousness or an autonomous motor loop.

`validate_neural_proposal(packet)` checks `mojum.neural-proposal/1` with explicit `backend=fixture|annarchy|brian2`, `mode=shadow|proposal`, source tick, virtual observation time, hex SHA-256 *revision identifier* (not a security signature), up to 64 typed scalar ports and an optional bounded virtual `(dx,dy)` motor **proposal**.

- `shadow` mode forbids submitting a motor proposal entirely.
- `proposal` mode allows a bounded suggestion only. Nothing in this schema authorizes applying it to MuJoCo or real hardware. A separate motor gate must confirm time, provenance, permissions and safety.
- No model binary, compiled neural library, third-party dependencies, Noosphere event writes or replay migration.
- Values are finite, no booleans as numbers, no duplicate/unknown port names or implicit new fields. Validated packets are detached from caller-owned dictionaries.
- Future ANNarchy implementation must independently demonstrate deterministic CPU neural population updates, STDP, exact state persistence on a disposable journal and held-out action-vs-frozen controls, with Brian2 differential reference.

Run `PYTHONPATH=src python -m unittest tests.test_neural_proposal -v`. Cross-module integration is deferred until independent acceptance.
