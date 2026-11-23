# Opt-in offline PCM16LE mono sample-rate conversion (R01)

Parent: [#694](https://github.com/Garyu-tt/mojum/issues/694). This is a narrow **offline PUBLIC_SYNTHETIC preprocessing helper**, not speech recognition, language understanding, a microphone capture path, a production VAD bridge, a neural auditory input, a real-time streaming adapter, a voice model or a privacy proof.

## Contract

`mojum.audio_pcm_resample.resample_pcm16le_mono` accepts **exact built-in `bytes`** holding signed **mono PCM16 little-endian**, exact source rate `16000`, `32000` or `48000`, explicit `opt_in=True`, and exact source label `PUBLIC_SYNTHETIC`. It returns a frozen `PCMResampleResult` with schema `mojum.audio.pcm16-resample/1`, source/target rates, exact source/output frame counts, converted bytes and SHA-256 of converted bytes. No I/O is performed; there are no device, filesystem, network or third-party imports.

- Conversion **must not be triggered by default**: `opt_in=False` rejects input; unsupported source-label declarations reject input. Labels/opt-in are **assertions by the caller** and do not verify consent or provenance. A higher-level adapter must implement its own consent, access control and privacy enforcement.
- Input must be nonempty, even byte length, <=10 seconds at its declared rate and contain an integer number of target 16 kHz frames. No partial-sample buffering, resampling-time clock guess, implicit padding, fractional frame truncation or source-rate inference. For arbitrary live chunks, buffering and a stateful stream contract must be designed and reviewed separately.
- Rate 16 kHz is byte-identical pass-through after *all* gates. Rates 32/48 kHz use integer-ratio decimation and a fixed, symmetric Hamming-windowed sinc low-pass FIR (49/65 taps, cutoff 7 kHz). Coefficients are pinned signed Q20 integers with sum exactly `1 << 20`; convolution, rounding (nearest with ties away from zero) and PCM16 clipping are integer-only, and byte decoding/encoding is explicitly little-endian. Boundaries use sample edge-hold, making the full-buffer result independent of operating-system endian and floating point math.
- The SHA-256 identifies **output bytes only**; it is not source authentication, a content license, an authenticated journal digest, nor a security boundary for real microphone input. Do not put raw speech in PUBLIC logs, issue comments or CI artifacts.

## Example (synthetic fixture only)

```python
import struct
from mojum.audio_pcm_resample import resample_pcm16le_mono

public_fixture = struct.pack("<6h", 0, 100, 200, 300, 400, 500)
result = resample_pcm16le_mono(
    public_fixture, 48000, opt_in=True, source_tag="PUBLIC_SYNTHETIC"
)
assert result.output_sample_rate_hz == 16000
assert result.output_frames == 2
assert len(result.pcm16le) == 4
```

## Reproduction and admission limits

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_pcm_resample.py' -v
```

Tests exercise explicit opt-in, hostile source and Python subclass values, malformed and over-budget payloads, integer alignment, 16 kHz identity, signed extremes, DC preservation, source provenance fields, deterministic replay, passband 1 kHz and stopband 10 kHz synthetic sines for both 32/48 kHz inputs.

The initial local hermetic Python 3.13.5 test suite passed 12/12; this is **not** exact-branch GitHub Actions 3.11–3.13 evidence. Neither sherpa-onnx nor whisper.cpp was imported or exercised. Acceptance requires independent review and exact-head CI; no automatic merge to `main`.

### DSP and future integration limits

The 7 kHz cutoff preserves speech content below ~7 kHz, but attenuates useful content approaching 8 kHz. Hamming FIR stopband attenuation is **finite**, not a guarantee for every input frequency or adversarial waveform. Edge-hold creates finite transient behavior near clip ends. There is no dithering, gain control, clipping detection, anti-spoof verification, stereo downmix, upsampling, arbitrary sample rate, or gapless processing across independently converted chunks. Accuracy, ASR word-error rate, CPU budget on Pixel/Termux and robust real-microphone performance **remain unmeasured**. This module cannot claim to enforce a 10-second *execution deadline*, only a 10-second *input-duration budget*.

The adapter is deliberately not wired to `Aquarium`, Noosphere PRIVATE journals, motor control, native VAD/ASR, host services or hardware. Future integration must pin the exact source audio rate, absolute source timing, an authenticated privacy/consent decision and a measured CPU/latency envelope, then obtain separate QA.
