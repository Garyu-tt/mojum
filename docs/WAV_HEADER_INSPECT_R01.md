# WAV header inspector R01

Parent #694, implementation claim #897.

Pure-stdlib, CPU-first and offline. `inspect_wav(data: bytes)` accepts an exact immutable bytes object containing one complete little-endian RIFF/WAVE file (maximum 16 MiB). It validates one canonical 16-byte PCM format chunk and one data chunk in that order, mono, signed 16-bit, 8–192 kHz, byte-rate and block-alignment consistency, RIFF and chunk boundaries, even-sized samples, zero chunk padding, and a 128-chunk budget. Unknown ancillary chunks are skipped without interpretation.

Returns a small scalar-only report, including integer duration rounded down. No PCM payload, audio classification, transcription, file reading, microphone, network, device output or PRIVATE data. This is not a secure provenance attestation, a decoder, or a streaming parser. No existing runtime path is modified.

Run tests: `PYTHONPATH=src python -m unittest discover -s tests -p test_wav_header_inspect.py`.

Limits: 16 MiB caller-owned input already resident in RAM; unknown ancillary chunks may contain arbitrary bytes and are not exposed. No RF64, WAVE_FORMAT_EXTENSIBLE, compressed formats, multichannel or noncanonical fmt extensions. Independent QA and exact-head CI required before merge.
