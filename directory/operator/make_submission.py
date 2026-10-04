#!/usr/bin/env python3
"""Submit an agent entry to the Hermes-A2A directory.

Reads the operator's ed25519 private key from
~/.hermes/directory_operator.key (32 raw bytes, base64), canonicalizes
the submission payload (sorted keys at every level, no whitespace),
signs the canonical UTF-8 bytes, base64-encodes the signature, and
POSTs the envelope to https://hermes-a2a.dpmob.com/submit (or any
URL passed via --submit-url).

The canonicalization algorithm MUST match the directory's
submit.js function `canonicalize()` — keys sorted at every level,
JSON string values exactly per JSON.stringify (UTF-8, ensure_ascii
default), no whitespace.

The public key sent in the envelope is derived from the operator's
private key so it stays consistent with the allowlist baked into
submit.js.

Usage:
    python3 make_submission.py --name "My Agent" \\
        --description "Does useful things" \\
        --capabilities a2a_call --capabilities memory_share \\
        --card-url https://example.com/.well-known/agent-card.json

By default the agent_id is auto-generated as agent_<16 hex chars>.
Pass --agent-id to override.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import urllib.error
import urllib.request
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


DEFAULT_KEY_PATH = "/home/marc/.hermes/directory_operator.key"
DEFAULT_SUBMIT_URL = "https://hermes-a2a.dpmob.com/submit"
DEFAULT_CARD_URL = "https://hermes-a2a.dpmob.com/agents.json"


def canonicalize(obj: Any) -> str:
    """Recursive JSON canonicalize: keys sorted at every level, no
    whitespace. Strings are JSON-quoted by json.dumps with the default
    ensure_ascii=True (so non-ASCII gets escaped as \\uXXXX, matching
    JSON.stringify in modern V8). This mirrors the directory
    worker's canonicalize() function."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return (
            "{"
            + ",".join(json.dumps(k, ensure_ascii=True) + ":" + canonicalize(obj[k]) for k in keys)
            + "}"
        )
    raise TypeError(f"cannot canonicalize {type(obj)}")


def load_private_key(path: str) -> Ed25519PrivateKey:
    with open(path, encoding="utf-8") as f:
        b64 = f.read().strip()
    raw = base64.b64decode(b64)
    if len(raw) != 32:
        raise ValueError(f"expected 32-byte ed25519 seed, got {len(raw)} bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_b64(sk: Ed25519PrivateKey) -> str:
    raw = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "ed25519:" + base64.b64encode(raw).decode()


def make_agent_id() -> str:
    return "agent_" + secrets.token_hex(8)


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="Display name for the agent")
    ap.add_argument("--description", default="", help="One-paragraph description")
    ap.add_argument("--capabilities", action="append", required=True,
                    help="Capability tag (repeat for multiple, e.g. --capabilities a2a_call --capabilities memory_share)")
    ap.add_argument("--card-url", default=DEFAULT_CARD_URL,
                    help="Agent Card URL (must respond 200 to HEAD). Defaults to this directory's agents.json so the submission is self-referential and passes the liveness check.")
    ap.add_argument("--agent-id", default=None,
                    help="Override the auto-generated agent_id (default: agent_<16 hex>)")
    ap.add_argument("--declared-at", default=None,
                    help="ISO-8601 UTC timestamp. Default: now.")
    ap.add_argument("--public-key", default=None,
                    help="Override the public_key sent in the envelope. Default: derived from the operator private key.")
    ap.add_argument("--key-path", default=DEFAULT_KEY_PATH,
                    help=f"Path to operator private key (default: {DEFAULT_KEY_PATH})")
    ap.add_argument("--submit-url", default=DEFAULT_SUBMIT_URL,
                    help=f"POST target (default: {DEFAULT_SUBMIT_URL})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the envelope and print it, don't POST")
    args = ap.parse_args()

    sk = load_private_key(args.key_path)
    pk_b64 = args.public_key or public_key_b64(sk)

    payload: dict[str, Any] = {
        "agent_id": args.agent_id or make_agent_id(),
        "name": args.name,
        "agent_card_url": args.card_url,
        "public_key": pk_b64,
        "capabilities": args.capabilities,
        "description": args.description,
        "declared_at": args.declared_at or utc_now_iso(),
    }

    canonical = canonicalize(payload)
    sig = sk.sign(canonical.encode("utf-8"))
    envelope = dict(payload, signature=base64.b64encode(sig).decode())

    print("---- canonical bytes (the exact thing signed) ----")
    print(canonical)
    print("---- envelope to POST ----")
    print(json.dumps(envelope, indent=2))

    if args.dry_run:
        print("---- --dry-run: not POSTing ----")
        return 0

    body = json.dumps(envelope).encode("utf-8")
    req = urllib.request.Request(
        args.submit_url,
        data=body,
        headers={
            "content-type": "application/json",
            "user-agent": "hermes-a2a-operator-submitter/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            response_body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        response_body = e.read().decode("utf-8", errors="replace")

    print(f"---- POST {args.submit_url} -> HTTP {status} ----")
    print(response_body)
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())