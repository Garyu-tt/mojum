# Mojum motor batch validation R01 (issue #5)

This standalone CPU-first, stdlib-only **advisory validation** contract extends the existing pure-data MotorGate. It is not MuJoCo, joint calibration, an actuator controller, an atomic reservation, or a physical safety certification. Default Aquarium behavior and durable journal formats are unchanged.

## Contract

The function **validate_motor_batch(gate, intents, expected_revision=rev)** accepts a bounded list/tuple of 1–128 **distinct** MotorIntent values, never exceeding the number of joints in the gate. Every command must match the gate's current revision and respect its existing finite target, joint-limit and e-stop checks. Unknown joints, duplicate joints, invalid UTF-8/oversized names, forged types, stale revisions and invalid values reject the **whole validation call**. No partial receipt is returned and the function never mutates motor state.

On success it returns an immutable **MotorBatchReceipt** with schema **mojum.motor.batch-validation/1**, revision, commands, sha256; commands are sorted lexicographically by joint. Targets are normalized by MotorGate to finite float; -0.0 normalizes to 0.0. SHA-256 is computed over a canonical UTF-8 JSON envelope with schema, revision and ordered commands. This is a reproducibility checksum **not a signature, authorization token, lease or evidence of execution**. It is unsafe to deserialize and trust an arbitrary receipt without revalidation of intents and current gate.

The gate's e-stop and revision are checked before and after the validation loop. This narrows accidental stale acceptance in synchronous code but does **not** create a lock or cross-thread, cross-process or cross-device atomicity. Any real executor must independently own synchronized revision fencing, all-or-nothing execution semantics, physics constraints and e-stop/hardware isolation. Do not interpret acceptance of a batch as permission to actuate.

## Tests

From the checkout, run:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_motor_batch.py' -v

Regressions cover canonical ordering/hash, limits, duplicate rejection, stale gate/intent revision, e-stop, revision/e-stop changes *during* synchronous validation, invalid/oversized identifiers, nonfinite/boolean/huge target values, frozen receipts and the full 128-joint boundary. No networking, connected rig or PRIVATE Noosphere needed.

**Merge gate:** independent review and exact-head Python 3.11–3.13 Aquarium/Motor/Mesh CI required. This branch is a proposed additive Draft slice, not accepted for production or real devices.
