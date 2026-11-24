# External-agent heartbeat key lifecycle R0.1

Opt-in stdlib metadata policy for issue #717. It selects one active
HMAC-SHA256 signing key and admits bounded historical verification across
rotation, retirement, retention expiry and revocation.

- Only the `agent-heartbeat` purpose and `HMAC-SHA256` algorithm are accepted.
- At most 64 exact-dict metadata records; IDs and timestamps are bounded.
- Non-revoked signing windows may not overlap.
- Retired keys cannot sign new events. Every retired key must carry an explicit
  `verify_until` timestamp; historical verification fails after that bound.
- Revocation immediately blocks signing and verification, including history.
- Keyring metadata and its consistency digest are revalidated at every public
  operation. Python frozen dataclasses are not treated as tamper-proof.
- Unknown keys, future events and expired retention fail closed.
- The SHA-256 digest is a consistency fingerprint, not authentication.

The caller must obtain secret bytes from its own protected key store and run
HMAC separately. This module never accepts, generates, stores or returns key
material. It does not provide key storage, secure deletion, rotation transport,
device identity, network ingress, replay protection or Cezar integration.
