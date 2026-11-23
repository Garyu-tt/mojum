# Optional sherpa-onnx offline ASR adapter R0.1 (#694)

Independent opt-in integration of **already supplied PUBLIC_SYNTHETIC**
mono 16-kHz PCM16 WAV with an externally configured local
`sherpa_onnx.OfflineRecognizer`. It does not listen to microphones, read
paths, fetch models, spawn processes, open sockets, modify the Noosphere,
or move speech into the canonical GENESIS/School runtime.

## Usage and explicit admission

A trusted local runner must separately install a pinned sherpa-onnx runtime,
verify its model and token assets/license and configure its recognizer. For
example, upstream provides `sherpa_onnx.OfflineRecognizer.from_transducer`
and `from_whisper` with explicit local model/token paths. Those are the
**caller's** responsibility, never accepted as untrusted input here.

```python
from mojum.sherpa_offline_asr import transcribe_public_wav

# recognizer: pre-created, trusted sherpa_onnx.OfflineRecognizer
# wav_bytes: authorized, explicitly PUBLIC_SYNTHETIC, no private organism data
report = transcribe_public_wav(
    wav_bytes, recognizer, enabled=True, visibility="PUBLIC_SYNTHETIC"
)
```

Actual engine entrypoints used: `recognizer.create_stream()`,
`stream.accept_waveform(sample_rate, float_samples)`,
`recognizer.decode_stream(stream)`, `stream.result.text`. Each input is
normalised to [-1, 1] float32. Returns a versioned
`mojum.sherpa-offline-asr/1` report with frame count, duration,
PCM SHA-256 and a bounded transcript. **No learning, VAD, TTS, consent
verification or production audio path is implemented.** The SHA is provenance
only, not an authenticity proof.

The ASR engine is optional. The stdlib package loads with no NumPy or sherpa
installed; NumPy is preferred if installed, and the stdlib float buffer is
used for offline stub tests when NumPy is absent.

## Security/budget boundaries

- Default invocation is refused; only literal `enabled=True` and explicit
  `PUBLIC_SYNTHETIC` are accepted, never PRIVATE input.
- WAV is supplied as bytes, max 400 kB, 10 seconds / 160,000 frames, mono,
  uncompressed 16-kHz 16-bit PCM. Invalid format, truncation and empty
  payload fail closed before recognizer use.
- Transcript max 500 characters; control characters, invalid Unicode and
  non-text results are refused. No logs or disk/network I/O.
- This module **does not** prove or enforce a real OS resource limit on the
  native C++ recognizer. Production experiments require an isolated process
  with CPU/RSS/timeout/network/file sandbox limits and explicit consent.
- Tested using a synthetic public audio fixture and fake local recognizer.
  No actual sherpa model, live microphone, external network or model assets
  were available/tested. ASR accuracy and resource performance are unknown.
- This slice is independent of #694's speech-observation schema, whisper.cpp
  comparator and PCM16 energy precursor PR #708; no overlapping files.

## Reproducibility, licenses

Reference upstream `k2-fsa/sherpa-onnx` master observed at
`4137e83f8a678040b5ad15db91a1ab47dba05a73` (2026-11-23);
Apache-2.0 code license. Pin an actual release + model checksum,
runtime/ABI, dependent model and dataset licenses for any execution.
No upstream code or models are bundled.

Run offline tests: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_sherpa_offline_asr.py' -v`.

This is a **Draft PR candidate only** until exact-head CI and an independent
QA review. No automatic merge, real device deployment or school integration.
