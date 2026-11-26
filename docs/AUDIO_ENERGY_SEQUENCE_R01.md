# Audio energy sequence R0.1 (issue #866)

**Status:** isolated, opt-in, stdlib-only CPU fixture. Parent #694 remains open.
This is *not* sherpa-onnx, voice activity detection, speech recognition,
transcription, a microphone capture service, a learned model, or TTS.

## Contract

**mojum.audio_energy_sequence.measure_pcm16_sequence** accepts frames and
keyword options: start_ns, clock="simulated", origin="synthetic",
threshold=0.035. It wraps the existing single-frame
**mojum.audio_energy.measure_pcm16_frame** without changing it.

* Input must be a **built-in list or tuple** of 1..256 immutable bytes
  objects, each exactly **640 bytes**: mono PCM16 little-endian, 16 kHz,
  320 samples / 20 ms. Maximum accepted raw input: 163,840 bytes (5.12 s).
  Iterators, memoryviews, bytearrays, subclasses, malformed/partial frames,
  and unbounded streams are rejected, not consumed.
* **start_ns** is a caller-supplied integer in [0, 2^63-1], excluding bool.
  Frame i receives start_ns + i * 20,000,000; overflow of the final
  observed timestamp is rejected before measurement. No wall-clock reads.
  This presumes contiguous input; missing samples, dropped device frames
  and the correctness of monotonic timestamps **cannot** be detected here.
* The underlying single-frame meter validates threshold (finite in (0,1]),
  clock (simulated/monotonic), and origin
  (synthetic/microphone-opt-in). Simulated time cannot claim microphone
  provenance. Actual user permission and source timestamps must be verified
  separately by any future opt-in acquisition boundary.
* Output schema **mojum.audio.energy-sequence/1** is a JSON-compatible
  **PRIVATE** scalar report: individual frame timestamps, normalized RMS
  and peak, threshold-gate booleans, aggregate gate count and maximum RMS,
  fixed sample-rate/byte/duration metadata, and a SHA-256 over the
  canonical JSON scalar report (excluding the sha256 field).
  raw_audio_retained is always false. No original PCM, raw samples,
  waveform, file paths, transcript, speaker ID, or secrets are emitted.
  A digest attests **neither PCM bytes nor consent nor authenticity**:
  different waveforms can have the same RMS/peak observations.

## Offline reproduction

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_energy_sequence.py' -v
~~~

~~~python
from mojum.audio_energy_sequence import measure_pcm16_sequence

frame = bytes(640)
report = measure_pcm16_sequence((frame, frame), start_ns=0)
assert report["frame_count"] == 2
assert report["open_gate_frames"] == 0
assert report["last_observed_at_ns"] == 20_000_000
assert report["raw_audio_retained"] is False
~~~

Tests cover deterministic replay/digest, direct consistency with the
single-frame meter, threshold equality, 256/257-frame resource boundaries,
the signed-63-bit time boundary, invalid timestamps/clock/provenance,
nonfinite and oversized thresholds, bad first/late frames, and no
partial measurement on preflight failures.

## Safety / integration

No network, dynamic imports, file I/O, native engine, physical actuator,
SQLite journal, Noosphere read/write, or mandatory dependency. Both
synthetic and caller-provided opt-in microphone observations are marked
PRIVATE because even scalar energy patterns can leak behavioral information.
The module does not acquire consent, establish trustworthy clock continuity,
calibrate noise, or classify speech. It is a bounded **offline processing**
building block, not a production audio ingestion or retention policy.

PR must remain Draft until exact-head CI across supported Python versions
and independent review. Parent #694 remains incomplete: native backend
accuracy, Android latency, real VAD/ASR, hardware measurements, licensed TTS
and GENESIS integration are separate acceptance gates.
