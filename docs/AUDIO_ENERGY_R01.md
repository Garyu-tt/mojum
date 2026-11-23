# Audio energy frame adapter R0.1 (issue #694)

Small, **functional** CPU/stdlib-only sensor precursor for future offline sherpa-onnx/whisper.cpp adapters. It accepts exactly one immutable 20 ms, 16 kHz, mono little-endian signed PCM16 frame and emits measured RMS/peak and a configurable energy flag; no ASR, VAD probability, transcribed words, speaker recognition, model loading, audio capture or TTS is provided.

```python
from mojum.audio_energy import measure_pcm16_frame

frame = bytes(640)  # synthetic silence, not microphone capture
result = measure_pcm16_frame(frame, observed_at_ns=0)
assert not result["energy_gate_open"]
```

Input provenance must be explicit: `origin=synthetic` for simulated data (default) or `origin=microphone-opt-in` with `clock=monotonic`. The latter is a **label**, not proof that consent exists. A real microphone and any third-party model may only be attached after separate local permission, language, storage/consent and resource QA. Never confuse the heuristic energy flag with recognized speech or inferred emotion.

No bytes are retained or persisted, no external library/network/OS device is accessed, and no body actuation can occur. The current synthetic Aquarium and Noosphere storage formats are unchanged.

For offline CPU tests, run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_energy.py' -v`.

Future PR: separately installed sherpa-onnx processes these frames (or converts format deliberately), emits timestamped speech observations subject to source/visibility authorization and is benchmarked against whisper.cpp; stay opt-in and fail closed when unavailable.
