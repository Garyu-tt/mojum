# Offline episode transition fingerprint R0.1

Parent #690; claimed issue #1157.

`mojum.episode_fingerprint.fingerprint_episode(transitions, episode_id=...)` computes a deterministic SHA-256 content witness for an explicitly supplied **PUBLIC_SYNTHETIC** episode. This is an advisory reproducibility comparison, **not** a signature, physics validator, privacy scrubber, Gymnasium implementation, or a proof of authentic execution.

Input: nonempty list/tuple of up to 10,000 transition mappings. Each transition has exactly `tick`, `observation`, `action`, `reward`, `terminated`, `truncated`. Ticks must be consecutive from zero. Terminal flags are booleans; if present before the final transition, reject. Reward must be finite numeric. Observations/actions may contain bounded JSON-compatible structures. Maximum serialized payload: 4 MB. Mapping keys are sorted; each record is length-framed and hashed; episode ID and schema are domain-separated.

Output contains schema, episode ID, transition count and hex SHA-256 digest, **not** the observations or actions. Do not pass private/secret material: hashing does not anonymize it. No environment access, files, execution, networking or optional dependencies.

Run: `python -m unittest discover -s tests -p 'test_episode_fingerprint.py'`.
