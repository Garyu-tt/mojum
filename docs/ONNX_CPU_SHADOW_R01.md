# Optional pinned ONNX Runtime CPU shadow adapter (#757)

Status: **draft / not accepted**, no production activation. This is an isolated adapter for a **trusted, pre-provisioned, public synthetic** numeric feed. It does not implement ANNarchy/Brian2 STDP (#691), sherpa-onnx ASR (#694), learning, real motor dispatch, or canonical graph/journal changes.

## Usage and contract

src/mojum/onnx_cpu_shadow.py has **zero mandatory dependencies** and imports NumPy/ONNX Runtime only after explicit enabled=True and strict SHA-256 model verification.

Run offline regression:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_onnx_cpu_shadow.py' -v

For trusted local model inference, install separately in an isolated, network-disabled environment:

    python -m pip install 'onnxruntime==1.32.0'

NumPy is a runtime dependency. Create a ShadowSpec with:
- schema="mojum.onnx-cpu-shadow/1"
- trusted .onnx file SHA-256
- exactly one input/output tensor name, each pinned
- static single-batch [1, features] / [1, outputs] tensor(float) metadata
- runtime_version="1.32.0"

Call create_shadow_runner(path, spec, enabled=True) then runner.predict([finite_float, ...], source_classification="PUBLIC_SYNTHETIC"). Scores are explicitly SHADOW_ONLY: they cannot execute an action or bypass motor admission. Real model weights, an actual .onnx fixture, and ORT/NumPy are deliberately not checked in. This PR does **not** claim a real inference run, only contract and fake-runtime regression.

## Threat model and isolation

- Accept **trusted models only**. Model SHA-256 pins exact bytes but does **not** authenticate the publisher, license, provenance, external weights, operator content or safety of native parsers. Never load untrusted models into an organism process. Run real inference in a sandboxed child with host-enforced CPU, RSS, disk/network limits before production adoption. ORT may support external-data references; loading model bytes is not proof of filesystem isolation.
- Reject symlink paths (POSIX O_NOFOLLOW where supported), non-regular files, empty/oversize files (16 MiB), changed content and digest mismatch. A Windows path race is not formally eliminated. External asset access must receive a separate security review.
- CPUExecutionProvider only, one intra/inter-op thread, fallback disabled, exact tensor names and static float32 [1,N] shapes. Logical budgets: 1–512 feature values, 1–64 output values, finite bounded numeric values. This does **not** constitute a hard CPU time or RSS quota; model scratch allocations can exceed the model file size.
- Calls reject source_classification values other than PUBLIC_SYNTHETIC. This caller-supplied marker is **not** a provenance/security oracle; any future adapter must enforce classification with independent authorization. No PRIVATE Noosphere records, SQLite journals, host IDs, credentials, real audio/hardware or network RPC are handled.
- Regression tests cover changed file, digest, symlink, manifest/version, provider, tensor metadata, nonfinite input/output, classification and deterministic fake replay. Real trusted-model smoke, CPU/RSS benchmark, ONNX external-data audit, package ABI, actual model license/asset provenance and independent exact-head QA are outstanding.

## Upstream pin / licensing

- Candidate ONNX Runtime 1.32.0, PyPI release 2026-11-13, MIT license stated upstream. Package/transitive notices and compatibility with the Mojum project license remain to be reviewed: https://pypi.org/project/onnxruntime/1.32.0/ and https://onnxruntime.ai/docs/api/python/api_summary and https://github.com/microsoft/onnxruntime .
- CPU resource estimate is single ORT session + weights + scratch and **has not been measured**. Model file bytes ≤16 MiB, input ≤512 float32, output ≤64 float32. Peak RSS, latency, operator allocation and supported platform/ABI remain unknown until measured with a real model.

**No merge into main** without independent exact-head CI and QA approval.
