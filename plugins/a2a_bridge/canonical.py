"""Canonical JSON for A2A handshake envelopes.

Both the local signer and the remote verifier must produce byte-
identical canonical bytes for the same logical envelope. We sort
keys at every level, use ``ensure_ascii=False`` so non-ASCII names
don't get escaped to ``\\uXXXX`` (and match the Worker's choice on
the directory side), and exclude the ``signature`` field from the
canonical bytes used as the signed message.
"""

from __future__ import annotations

import json
from typing import Any


def canonicalize(obj: Any) -> str:
    """Recursively serialize with sorted keys and unescaped Unicode."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(obj[k]) for k in keys) + "}"


def canonical_bytes(envelope: dict) -> bytes:
    """Canonical bytes for an envelope, with ``signature`` excluded.

    The signer computes the signature over these bytes; the verifier
    recomputes them from the received envelope (after dropping the
    ``signature`` field) and checks. Both sides must produce the
    exact same bytes.
    """
    signed = {k: v for k, v in envelope.items() if k != "signature"}
    return canonicalize(signed).encode("utf-8")
