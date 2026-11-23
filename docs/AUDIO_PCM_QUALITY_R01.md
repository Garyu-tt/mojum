# Mojum #694: offline PCM16 signal-quality preflight R0.1

## Purpose and ownership

`mojum.audio_pcm_quality.analyze_pcm16_quality` is an **explicitly opt-in**, bounded, *read-only* scalar diagnostic on caller-supplied **PUBLIC_SYNTHETIC** PCM16LE mono 16-kHz audio. It is intentionally separate from existing `audio_energy` (single-frame RMS gate), the PCM energy sequence, WAV parser, downmixer, resampler, VAD, sherpa-onnx and whisper.cpp adapters. This module does **not** integrate into the runtime by default, analyze recordings, or authorize hardware access.

No audio waveform is returned, logged or stored by the function. `origin="PUBLIC_SYNTHETIC"` is a **caller assertion, not authenticated provenance**. Supplying real/private audio under a forged label breaks the caller's contract; the module cannot prove the label.

## Contract

```python
from mojum.audio_pcm_quality import analyze_pcm16_quality
result = analyze_pcm16_quality(
    pcm16le_bytes,
    sample_rate_hz=16000,
    origin="PUBLIC_SYNTHETIC",
    enabled=True,
)
```

Default `enabled=False` fails closed. The input must be an exact `bytes` object (not subclasses, buffers, strings or bytearray), with 1–500 complete contiguous 20-ms frames (640–320,000 bytes), exactly 16,000 Hz, caller asserted public synthetic data. All bounds are checked *before* decoding. No I/O, subprocess, dependency, network, microphone, model load, clock reads, journal writes or runtime/policy changes.

The deterministic result uses schema `mojum.audio.pcm-quality/1` and contains only scalars and simple flags:

- `min_sample`, `max_sample`, `rail_hit_count` and `longest_identical_rail_run`: rails are exactly -32768 or +32767. Three or more adjacent identical rail samples flag `possible_hard_clipping`, **not proof of clipping**. Isolated rail hits do not automatically trigger the flag.
- `dc_mean_lsb`: integer sample average rounded to nearest, ties away from zero. `possible_dc_offset` is flagged when `abs(sum(samples))*100 >= count*32768*2` (exact signed arithmetic, 2% full scale), **not proof of DC hardware failure**.
- `zero_crossings`: adjacent *nonzero* polarity changes, skipping zeros. Not a speech rate or VAD output.
- `digital_silence` means every sample is exactly zero; `flatline` means every sample has the same nonzero value. Both can arise from legitimate synthetic inputs.
- `sample_count`, `frame_count`, `duration_ns`: 62,500 ns per sample, no wall clock. All observations are diagnostic only (`classification="signal-diagnostics-only-not-speech"`, `raw_audio_retained=False`).

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_pcm_quality.py' -v
python -m compileall -q src/mojum/audio_pcm_quality.py tests/test_audio_pcm_quality.py
```

15 hermetic offline tests cover opt-in and source gates, forged types, 20-ms alignment, 10-second cap, silence, DC, negative rails, possible clipping, sign changes through zero, exact threshold, deterministic JSON and capacity. This does not establish that real ASR accuracy or microphone/audio hardware improved; real-world signal calibration requires separately authorized experiments. Any production or PRIVATE-audio ingestion requires a separate reviewed consent boundary and explicit wiring.
