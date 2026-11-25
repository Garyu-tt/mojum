# VRM asset provenance policy R0.1

## Purpose

This opt-in, offline admission helper validates metadata for a locally staged VRM asset and its declared renderer dependencies. It is deliberately separate from avatar loading and rendering.

## Contract

Python 3.11+, standard library only. Call `validate_manifest(manifest, policy)` with exact built-in dictionaries and lists. The manifest declares:

- schema `mojum.vrm-asset-policy/1` and provenance `PUBLIC_SYNTHETIC`;
- a relative, traversal-free staged path, SHA-256 digest, positive declared byte size, allowlisted media type, source identifier and allowlisted license;
- dependencies with exact semantic versions and allowlisted licenses.

The caller supplies explicit allowlists and resource limits. The result is a detached normalized manifest, sorted violation identifiers, an acceptance boolean and SHA-256 digest of the normalized metadata. Dependency ordering does not affect the digest.

Source identifiers are bounded local tokens such as `local-import`; colons and
path separators are rejected, including URI-scheme and drive-relative forms.
Policy allowlists accept exact built-in lists or tuples. Collection admission
uses identity checks before length or iteration, so custom metaclass equality
and collection hooks are not invoked for rejected inputs.

## Security and limits

The validator rejects remote/path-like sources, traversal, absolute paths, malformed hashes, mutable versions, duplicate/unapproved dependencies, unsupported media types/licenses, malformed containers and out-of-policy sizes. The helper does not read the path, verify the digest against file bytes, prove the declared byte size, parse VRM, inspect license texts, fetch dependencies, or establish legal compatibility. All allowlists must be reviewed and supplied by the caller.

No filesystem, network, three-vrm, browser, SQLite, PRIVATE Noosphere, runtime wiring, body mutation or motor authority is used. Metadata validation is not proof that an asset is benign or licensed for every use.

## Validation

Run:

```sh
python -m pip install --no-deps -e .
python -m unittest discover -s tests -p 'test_vrm_asset_policy.py' -v
```

The dedicated GitHub Actions workflow runs the focused stdlib suite on CPython 3.11, 3.12 and 3.13.
