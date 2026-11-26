# PCM16 DC-offset diagnostic R01 (opt-in)

Parent: [Mojum #694](https://github.com/Garyu-tt/mojum/issues/694).
Reservation: [#965](https://github.com/Garyu-tt/mojum/issues/965).
Schema: `mojum.audio.dc-offset-diagnostic/1`.

## Scope and trust boundary

`mojum.audio_dc_offset.inspect_pcm16_dc_offset` is an isolated stdlib-only,
offline, deterministic read-only **diagnostic** of caller-owned
**PUBLIC_SYNTHETIC** PCM16 little-endian mono bytes at **16,000 Hz**.
It is not a capture device, ASR, VAD, speech detector, consent mechanism,
real microphone pipeline, physics, or learned behavior. It never normalizes,
modifies or returns audio and does not persist a waveform, transcript or file.
It does not admit PRIVATE, arbitrary microphone or live-source data.

Caller must explicitly supply `classification="PUBLIC_SYNTHETIC"`.
Only `bytes` (not a mutable bytearray or memoryview) are accepted.
The admitted range is 1–160,000 samples (2–320,000 bytes, ten seconds).
Malformed/odd/empty/oversized input and non-integer or non-16k sample rates
fail closed before decoding or hashing. An exact 1-sample frame is allowed;
there is no 20-ms framing requirement.

## Measurements

Integer-domain `sum_samples`, `sum_squares`, min/max, zero and clipped counts
are reproducible. Clipped count means **exactly** -32768 or +32767; it does
not prove that real recording saturation occurred.

For N decoded signed samples x[i] and scale S=32768:
- `dc_mean_normalized = sum(x) / (N * S)`
- `rms_normalized = sqrt(sum(x*x)/N) / S`
- `ac_rms_normalized = sqrt(N*sum(x*x) - sum(x)^2) / (N*S)`
- `dc_fraction_of_rms = abs(sum(x))/sqrt(N*sum(x*x))`
  (defined as zero for all-zero input).

Square root/normalized results are finite floating-point diagnostics, not
bitwise-exact decimal measurements or calibrated DC warnings. Only integer
sums/counts are exact. `duration_ns` is exact because a 16k sample is 62,500 ns.
The SHA-256 is an **integrity fingerprint of the supplied public fixture**,
not evidence of origin, authenticity, a signature or user consent. The report
is detached and contains no raw audio.

## Example

```python
import struct
from mojum.audio_dc_offset import inspect_pcm16_dc_offset

pcm = struct.pack("<320h", *([16384] * 320))
metrics = inspect_pcm16_dc_offset(
    pcm, classification="PUBLIC_SYNTHETIC")
assert metrics["dc_mean_normalized"] == 0.5
assert metrics["ac_rms_normalized"] == 0.0
```

Run: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_dc_offset.py' -v`.

## Integration limits

Opt-in helper only. No changes to `audio_energy`, resampling, downmix, gain,
sherpa/whisper, TTS, CLI, Aquarium, Noosphere or PRIVATE journals.
The helper is **not** wired into a live device or autonomous agent. Any later
integration requires explicit policy review, consent handling and independent
exact-head QA on Python 3.11–3.13 before release.
