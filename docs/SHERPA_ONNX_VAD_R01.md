# Optional sherpa-onnx Silero VAD segmentation R0.1 (#694)

## Scope

An opt-in native sherpa-onnx VAD adapter, separate from the already claimed ASR, whisper.cpp and PCM16 energy-frame work. The adapter produces candidate speech intervals only. It does not transcribe, recognize emotions/identity, connect to a microphone, execute actions, touch GENESIS, alter the canonical SQLite/Noosphere journals or transfer PRIVATE organism state.

The Python 3.11+ baseline has zero new mandatory dependencies. Normal import and deterministic tests work without numpy, sherpa-onnx or model files.

## Consent, input, output and budgets

- Every call requires enabled=True, consent=True, an absolute readable local ONNX path and a lowercase model SHA-256 pin.
- Only caller-prepared PUBLIC_SYNTHETIC signed 16-bit little-endian mono PCM at 16 kHz is supported. The timestamp denotes the first sample in a simulated nanosecond clock. The scope is a caller assertion, not proof of provenance or OS capture permission.
- A clip must have 512 to 160,000 samples (32 ms to 10 s); CPU provider and one native thread; 512-sample VAD windows; at most 64 candidate speech segments. Short final windows are zero-padded and clipped to real capture bounds.
- Validated output schema: mojum.speech.vad-segments/1. Half-open sample intervals carry absolute nanosecond timestamps and model SHA, but no raw audio, transcript, voice identity or original path.
- Non-monotonic/overlapping/invalid native segments, missing packages, model mismatch, malformed input and backend failures all fail closed without partial observations.
- The model must be an ordinary nonempty file of at most 16 MiB; symlinks, unnormalized paths and bad hash pins are rejected. The native engine subsequently reopens the file, so this is not a race-free immutable filesystem policy.

## Optional native runtime

Target pin: sherpa-onnx==2.0.3 plus a separately approved compatible numpy wheel. This target is a *declared version*, not a successfully tested production engine. Sherpa upstream uses Apache-2.0; Silero model bytes and their specific licensing/redistribution rights must be vetted independently.

The Python adapter uses upstream VadModelConfig / VoiceActivityDetector, setting Silero threshold=0.5, min speech=0.25 s, min silence=0.25 s, max speech=5 s, sample rate 16 kHz, window size=512 and provider cpu. It feeds bounded float32 windows, drains front/pop after accept_waveform and flush, and emits only validated intervals.

Example with an independently prepared and approved local Silero model:

    from mojum.sherpa_vad import segment_public_pcm16
    result = segment_public_pcm16(
        bytes(32000),
        observed_at_ns=0,
        model_path="/absolute/vetted/silero_vad.onnx",
        model_sha256="YOUR_ACTUAL_64_CHAR_LOWERCASE_SHA256",
        enabled=True,
        consent=True,
    )
    print(result["segments"])

Run zero-native-dependency tests:

    PYTHONPATH=src python -m unittest discover -s tests -p test_sherpa_vad.py -v

Do not put the ONNX file in the repository, install models automatically, or run untrusted models within the main organism process. For independent exact-head QA: execute in a network-denied immutable-model sandbox; measure p50/p95 CPU, RSS, silence/voice errors, repeated deterministic synthetic stimuli, model hash replacement and aborts, and check wheel ABI on Python 3.11-3.13 and Android separately. The fake backend tests prove only the adapter's safety boundary and sample/segment contract, not native recognition accuracy or measured performance. When test-only _sherpa/_numpy/_backend_version injection is used, the output is explicitly tagged `unverified-test-double-vad`, never the real native backend. A partial injection fails closed.

Upstream references:

- https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/vad-remove-non-speech-segments-from-file.py
- https://github.com/k2-fsa/sherpa-onnx (Apache-2.0)
- https://pypi.org/project/sherpa-onnx/2.0.3/
- https://k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html

Status: review candidate, not production integration or release acceptance. No direct main merge.
