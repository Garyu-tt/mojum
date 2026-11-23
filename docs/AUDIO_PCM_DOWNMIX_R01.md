# Strict offline stereo PCM16 downmix (issue #694, additive R0.1)

`mojum.audio_pcm_downmix.downmix_stereo_pcm16le` converts **caller-provided PUBLIC_SYNTHETIC** interleaved signed 16-bit little-endian left/right frames to signed 16-bit little-endian mono. This is a deliberately isolated preprocessing component. It does **not** read WAV headers, resample audio, use microphone APIs, decode compressed files, upload anything, write a journal, infer speech, or grant consent.

```python
from mojum.audio_pcm_downmix import downmix_stereo_pcm16le

stereo_silence = bytes(320 * 4)  # two channels × 320 samples × 2 bytes
result = downmix_stereo_pcm16le(stereo_silence, sample_rate_hz=16_000)
assert result.frames == 320 and len(result.pcm16le) == 640
assert result.sample_rate_hz == 16_000
```

Each output sample is the integer mean of the two channel values; a half-unit tie is rounded **toward zero**, not toward negative infinity. Pure integer arithmetic prevents float drift; input and output frame counts match exactly. No normalization, gain correction, antialiasing, sample-rate conversion, noise suppression or dither occurs. Anti-phase signals may cancel, which is expected and is not proof of absent speech. The sample rate is caller-declared because raw PCM has no sample-rate header. It is echoed, not independently verified.

**Safety and budgets:** requires exact `bytes` (not mutable buffers), whole nonempty 4-byte stereo frames, 8,000–192,000 Hz exact integer rate, at most **640,000 input bytes / 160,000 frames**, and at most **10 seconds** at the declared rate. Admission is checked before creating the output buffer. The immutable result contains at most 320,000 output bytes and has a versioned schema `mojum.audio.pcm16-stereo-downmix/1`. The only accepted visibility string is `PUBLIC_SYNTHETIC`. The label is a *caller assertion*, not a permissions mechanism; do not feed private or live microphone material. A real capture pipeline needs separate authorization and privacy review.

This utility is intentionally disjoint from the concurrently claimed `audio_pcm_resample.py` **mono** resampler, WAV inspector, frame-energy observer, VAD, sherpa-onnx ASR and whisper.cpp comparisons. A future adapter may explicitly downmix **then** resample in a separately versioned opt-in pipeline, after it verifies source metadata and budgets. No default dependency or existing canonical Aquarium/Noosphere wire format changes.

Validation: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_pcm_downmix.py' -v`. Integration with external ASR/VAD/native audio and exact-head 3.11–3.13 CI requires independent QA before merge.
