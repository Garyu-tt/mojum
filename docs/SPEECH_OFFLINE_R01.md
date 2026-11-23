# #694: optional offline ASR observation boundary R0.1

Status: **opt-in sherpa-onnx Python API adapter**, not speech perception in the canonical Aquarium, not a microphone service, not online ASR, not TTS, and not a deployed/mobile performance claim. Core stdlib dependency set is unchanged. No audio or transcript is stored automatically.

## Explicit local usage

Provide a locally installed and separately verified sherpa-onnx model, and a caller-owned 16 kHz mono float PCM sequence. Model loading, microphone consent, input capture and model-file verification are deliberately outside Mojum core.

    import sherpa_onnx  # optional, never imported by Mojum itself
    from mojum.speech_offline import OfflineSpeechSession, SherpaOnnxOffline

    recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder="/local/model/encoder.onnx",
        decoder="/local/model/decoder.onnx",
        tokens="/local/model/tokens.txt",
        language="ru", num_threads=2, provider="cpu",
    )
    session = OfflineSpeechSession(
        SherpaOnnxOffline(recognizer), model_id="whisper-local-reviewed", enabled=True,
    )
    # samples: preauthorized, in-memory 16 kHz mono float PCM from trusted caller
    # event = session.transcribe(samples, sample_rate_hz=16000,
    #                            start_ms=0, language_hint="ru", consent=True)

The caller must provide explicit consent for **each** window and decide separately whether, where and under which permissions text may be consumed. Returned text is PRIVATE, untrusted sensor evidence, never a command. Do not pass it directly to Graph IR, an action-selection policy, logging, a telemetry provider or Noosphere without independently reviewed access controls.

## Constraints and verification

- Maximum 5 seconds/80,000 normalized float samples per non-overlapping final window at **exactly 16 kHz mono**. No implicit downmix/resampling, timestamps are monotonic relative milliseconds, no system clocks. Sample values must be finite in [-1,1]. Failed ASR leaves sequence/timeline unchanged.
- Silence below RMS 0.002 returns an empty silence observation without native inference; a non-silent empty recognition gives no_speech. This **does not prove** freedom from speech hallucinations or serve as a complete VAD/noise solution.
- A model response is limited to 1024 printable characters; backend exceptions are not propagated into event or exception text. No output probability/confidence is invented. Model fingerprint is caller-defined opaque identifier, **not** verified model provenance.
- The in-process native recognizer cannot be preempted reliably by Python; an untrusted or compromised native library could still touch the filesystem/network. Deployment must use an OS sandbox (offline network namespace, CPU/RAM/time limits), audited local models and a killable worker. This adapter itself never opens a mic/socket/file or invokes commands.
- Tested contract uses OfflineRecognizer.create_stream(), stream.accept_waveform(sample_rate, numpy.float32 samples), recognizer.decode_stream(stream) and stream.result.text. Unit tests use a fake recognizer, **not real model quality, streaming latency or Russian-language accuracy**.

## Dependencies, licensing, and follow-ups

Candidate native engine: [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), PyPI reference sherpa-onnx==2.0.3 (released 2026-11-22; Apache-2.0 indicated on PyPI). numpy is lazy and optional. Validate exact package wheel, CPU architecture, ABI, transitive licenses, **each ONNX model/license** and reproducible hashes before deployment. This repository does not install or pin any native wheel automatically.

Optional alternative [whisper.cpp](https://github.com/ggml-org/whisper.cpp) is MIT-licensed upstream; not wired yet. It needs an isolated process boundary, deterministic result parser and its own models/license review before implementation. Audio/TTS consent and the rights to particular voices are separate matters. Android/Pixel profiling, VAD, ASR multilingual/held-out tests, authorized sensor-to-GENESIS consumer and privacy-safe replay all remain open tasks.

Run: PYTHONPATH=src python -m unittest tests.test_speech_offline -v on Python 3.11+ with no ASR package, network or models.
