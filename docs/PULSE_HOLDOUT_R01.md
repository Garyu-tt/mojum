# Mojum PUBLIC_SYNTHETIC delayed-pulse holdout R0.1

Parent: #6. Reserved build issue: #963. This is an opt-in, offline, stdlib/CPU-only evaluation of the existing delayed-pulse learner, not a new neural backend.

## Reproduce

Run in the installed Mojum environment (no network or private data):

    python -m mojum.pulse_holdout --train 64 --holdout 32
    python -m unittest tests.test_pulse_holdout -v

Only the generated deterministic toy signal is used by the CLI. Importers can call `evaluate_holdout(frames, train_frames=N, classification="PUBLIC_SYNTHETIC")` with a strictly typed list or tuple of 3..2048 synthetic interoception frames. No PRIVATE journal replay, user content, room geometry, child health measurement, recording or external runtime is supported.

## Method / leakage boundary

1. Validate **all** frames before model training: exact `kind`, `observed_at_ns` and `heartbeat` keys only; within the heartbeat only integer `phase_milli` (0..999) and Boolean `pulse`; strictly increasing uint63 timestamps. Reject extra privileged fields rather than silently dropping them.
2. Train `PulsePredictor` only on the first `train_frames` (2 or more) in exact observation order. The first frame seeds the delayed features; training updates equal `train_frames - 1`.
3. Hash a frozen model snapshot, then evaluate each holdout pulse using **the preceding** frame's features and the fixed learned weights. No holdout `observe()` calls or parameter adjustments occur. For the first held-out label, the prior features come from the last *training* frame. No current or future pulse/phase can contribute to its own prediction.
4. Compare frozen predictive mean squared error to a causally legal `previous pulse` baseline on the same holdout labels. Preserve negative improvements; do not silently turn poor performance into success. Also report online training MSE separately, without comparing it as held-out quality.
5. Return only aggregate metrics, one first-frame diagnostic, model/input SHA-256 content fingerprints and an explicit synthetic-only interpretation. SHA-256 is **not** authentication or anonymization; do not use this on PRIVATE data.

Summation is explicitly `ordered` by default for replay stability, or opt-in `compensated`, rather than depending silently on the native host's Python float summation. All work is CPU-only with bounded corpus length and no imported accelerator or network library.

## Limits and QA

- Tiny 3-feature LMS shadow predictor, not a biological heartbeat model, clinical signal, real cortical learning, general competence or evidence of consciousness.
- The synthetic source is a deterministic arithmetic phase/pulse generator, not a real dataset. Strong predictive results cannot be generalized.
- The frozen audit is **not** an Aquarium history migration, service integration or test of future model drift; it does not mutate existing Noosphere, GENESIS, motor, sensory or schema authorities.
- Coverage includes input/provenance guards, no-peeking regression against changed holdout labels, frozen-model digest, causal baseline, bounded budget, deterministic output and CLI smoke.
- Submit to independent exact-head review and the repository's Python 3.11–3.13 CI. Do not self-merge into protected `main`.
