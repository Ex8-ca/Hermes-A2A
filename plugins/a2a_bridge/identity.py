"""Identity primitive for a2a-bridge.

In v0.1.x, identity is the agent's URL + bearer token combination. We
expose a stable ``agent_id_for(url)`` so that meeting records and audit
entries have a durable handle that survives token rotation, even if
the underlying URL changes. (When the URL changes, the agent_id
changes too — which is the same as rotating a key in v0.2.)

In v0.2, this module will gain an ed25519 keypair path (see
``../ROADMAP.md``). The function signature is stable across both
versions, so callers don't need to change.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for stable hashing.

    Lower-cases scheme + host, strips a trailing slash from the path,
    drops a default port (80 for http, 443 for https), drops the
    fragment and the userinfo. Query string is preserved because
    agents sometimes route through that.
    """
    if not url:
        raise ValueError("url is required")
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    netloc = (parts.hostname or "").lower()
    if not netloc:
        raise ValueError(f"url has no host: {url!r}")
    port = parts.port
    if port is not None:
        default = 80 if scheme == "http" else 443 if scheme == "https" else None
        if port != default:
            netloc = f"{netloc}:{port}"
    path = parts.path or ""
    # The URL root is canonically empty path. Strip "/" and any
    # trailing slashes so the root URL and a URL with an explicit
    # trailing slash collapse to the same identity.
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def agent_id_for(url: str, *, prefix: str = "agent_") -> str:
    """Stable, short agent identifier derived from ``url``.

    Format: ``agent_<16 hex chars>``. The same URL always produces
    the same ID. Different URLs produce different IDs with
    cryptographic-strength collision resistance (16 hex chars =
    64 bits; the chance of two distinct URLs colliding is
    ~5 × 10⁻²⁰ for a million URLs).

    In v0.2, this function will accept an optional ``public_key``
    argument and the agentId will be derived from the key, not the
    URL. The signature is forward-compatible.
    """
    normalized = normalize_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    short = digest[:8].hex()
    return f"{prefix}{short}"


def fingerprint_url(url: str) -> str:
    """Human-friendly short fingerprint for display in consent prompts.

    Returns the first 12 chars of the SHA-256 of the normalized URL,
    grouped as 4-4-4 for readability. Example: ``8f3a-7c2d-9b1e``.
    """
    normalized = normalize_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    h = digest[:6].hex()
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}"
