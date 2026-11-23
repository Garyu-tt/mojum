# Mojum #946: bounded offline PCM16LE gain (R0.1)

Parent: #694. Independent additive utility, **not** the sherpa-onnx, whisper.cpp, VAD, energy, resampling, stereo downmix or TTS implementation.

## Contract

`mojum.audio_pcm_gain.apply_pcm16_gain(pcm, *, sample_rate_hz, gain_numerator, gain_denominator=1, visibility=None)` operates on **native `bytes`** of *signed mono 16-bit little-endian PCM*. Explicit input rate must be one of 8/16/32/48 kHz, never inferred. Nonempty, even-length PCM is required; both **480,000 frames / 960,000 bytes total** and **10 seconds at the declared rate** are enforced *before sample iteration*.

Gain is an exact **nonnegative rational** `gain_numerator / gain_denominator` with native integer parameters: numerator >= 0, denominator 1..1,000,000, gain <= 4. No floating point is involved. Each sample is scaled and rounded to nearest integer; exact half ties round toward zero for both signs, then clamp to [-32768, 32767]. `clipped_samples` counts only values outside the representable range **before** clamp (a value equal to a rail is not counted). `changed_samples` counts actual modified output samples. The immutable metadata result exposes original/output absolute sample peaks in integer PCM units and the retained sample rate/frame count. The audio bytes are available only as `result.pcm` and excluded from its `repr`.

Caller must explicitly pass `visibility="PUBLIC_SYNTHETIC"`; omission, other labels and subclasses are rejected with `AudioGainError`. **This is a caller-provenance contract, not a consent verification mechanism.** Don't pass PRIVATE or real microphone recordings. No file access, network, microphone, model loading, playback, wall clock, hardware actuator, Noosphere write, normalization policy or speech classification occurs.

## Example

```python
import struct
from mojum.audio_pcm_gain import apply_pcm16_gain
raw = struct.pack("<3h", -20000, 0, 20000)
result = apply_pcm16_gain(raw, sample_rate_hz=16000, gain_numerator=2, visibility="PUBLIC_SYNTHETIC")
assert struct.unpack("<3h", result.pcm) == (-32768, 0, 32767)
assert result.clipped_samples == 2
```

## Verification / acceptance

Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_pcm_gain.py' -v`. Tests include sign/ties, rails, exact boundary, zero/max gain, frame/rate invariants, malformed types, forged provenance, byte and duration limits, deterministic repeats and result immutability. The entire #694 integration remains incomplete. Independent exact-head QA and CI required before merge. This module is offline and cannot guarantee native backend real-time performance or audio quality.
