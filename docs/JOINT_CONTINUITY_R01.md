# Joint continuity auditor R0.1

Offline standard-library QA helper for explicitly supplied PUBLIC_SYNTHETIC joint-position samples. Each sample has exactly an integer logical tick, non-empty rig revision, and mapping of joint IDs to finite numeric radians. Samples must be consecutive, preserve one rig revision, and contain exactly the same joint IDs.

The caller supplies a positive maximum per-tick angular displacement for every joint. Revolute wrap handling is opt-in; joints not explicitly declared revolute are compared linearly. Joint semantics are never inferred.

The report includes maximum observed step, first offending tick, stable violation ordering, and a SHA-256 consistency digest over normalized inputs and policy. The digest is not a signature or authentication proof.

Defaults cap inputs at 10,000 samples and 512 joints; caller limits may lower but never raise those caps. No I/O, network, clock reads, model loading, actuator access, physics simulation, or runtime wiring. Not a motor-safety interlock; it does not validate acceleration, torque, collisions, anatomical plausibility, or sensor provenance.

Run: PYTHONPATH=src python -m unittest tests.test_joint_continuity -v
