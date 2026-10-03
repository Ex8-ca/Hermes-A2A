#!/usr/bin/env python3
"""Generate an ed25519 keypair and a signed envelope for the directory
Worker to verify. The Worker's verifySignature is tested against this.

Usage:
  .venv-test/bin/python tools/make_test_envelope.py
Outputs:
  /tmp/dir_test_key_b64.txt   — the private key (32 bytes, seed only), base64
  /tmp/dir_test_pub_b64.txt   — the public key, base64 with "ed25519:" prefix
  /tmp/dir_test_envelope.json — a sample submission envelope
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def canonicalize(obj):
    """Mirror of the Worker's canonicalize: keys sorted at every level."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return "{" + ",".join(json.dumps(k) + ":" + canonicalize(obj[k]) for k in keys) + "}"


def main() -> int:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    raw_pub = pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    raw_priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    pub_b64 = "ed25519:" + base64.b64encode(raw_pub).decode()
    priv_b64 = base64.b64encode(raw_priv).decode()

    sample = {
        "agent_id": "agent_8f3a7c2d9b1e4f5a",
        "name": "Marc's Test Bot",
        "agent_card_url": "https://example.com:9900",
        "public_key": pub_b64,
        "capabilities": ["a2a_call", "memory_share"],
        "description": "Smoke-test envelope for the directory Worker.",
        "declared_at": "2026-10-04T00:00:00Z",
    }
    canon = canonicalize(sample).encode("utf-8")
    sig = sk.sign(canon)
    envelope = dict(sample, signature=base64.b64encode(sig).decode())

    Path("/tmp/dir_test_priv_b64.txt").write_text(priv_b64)
    Path("/tmp/dir_test_pub_b64.txt").write_text(pub_b64)
    Path("/tmp/dir_test_envelope.json").write_text(json.dumps(envelope, indent=2))

    print("wrote /tmp/dir_test_priv_b64.txt (32-byte seed, base64)")
    print("wrote /tmp/dir_test_pub_b64.txt (public key, ed25519:+base64)")
    print("wrote /tmp/dir_test_envelope.json (signed envelope)")
    print()
    print("canonical bytes used to sign:")
    print(" ", canon.decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
