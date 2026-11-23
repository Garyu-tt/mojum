# GENESIS body admission R0.1

This focused boundary hardening keeps the existing synthetic body projection and
wire schemas unchanged.

## Admission rules

- The somatic chromosome must be supplied as a native JSON object and is
  detached before it is returned by validation.
- The GENESIS phenotype must be a native JSON object with the released
  `mojum.genesis.phenotype/1` schema.
- Its `parameters` object must contain exactly the inherited GENESIS genes:
  `heartbeat_bpm`, `body_scale_milli`, and `sensory_gain_milli`.
- Every parameter is checked against the same integer bounds used by the
  canonical GENESIS contract. Booleans, floats, strings, missing keys, and
  unknown keys are rejected.
- Serialization and UTF-8 failures are normalized to `BodyContractError`.

These checks prevent partial or malformed phenotype payloads from being used
to compile a body while leaving the existing growth curve, dimensions, and
body schema unchanged. The growth curve remains a synthetic fixture, not
biological anatomy or calibrated biomechanics.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_body_genetics_admission.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_body_genetics.py' -v
```
