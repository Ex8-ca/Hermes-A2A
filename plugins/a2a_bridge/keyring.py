"""ed25519 keypair management for a2a-bridge.

Each agent owns one ed25519 keypair, generated on first run, stored
locally with restrictive permissions. The public key is published
in the Agent Card and used to derive the stable ``agentId`` — a
collision-resistant identifier that survives bearer-token rotation
and DNS changes.

Storage: ``~/.hermes/a2a_bridge/identity.key`` (mode 0600). The file
holds the 32-byte private seed, base64-encoded. Public key is derived
on every load — never written to disk separately, so the file is the
single source of truth.

Forward-compatibility: in v0.1.x the identity primitive also accepts
a URL and derives the agentId from a hash of the URL. v0.2 keys take
precedence; the URL fallback is kept so v0.2 → v0.1.x meetings don't
break. v0.3+ will likely remove the URL fallback once the directory
is populated.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

_PUB_PREFIX = "ed25519:"
_KEY_DIR = "a2a_bridge"
_KEY_FILE = "identity.key"


def _get_hermes_home() -> str:
    """Resolve the Hermes home directory, with a graceful fallback.

    Tries ``hermes_constants.get_hermes_home`` first; falls back to
    ``$HERMES_HOME`` or ``~/.hermes``. The fallback is what makes
    the test venv able to import this module without the live
    Hermes tree on PYTHONPATH.
    """
    try:
        from hermes_constants import get_hermes_home as _real

        return _real()
    except Exception:
        return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")


def _key_path() -> Path:
    return Path(_get_hermes_home()) / _KEY_DIR / _KEY_FILE


@dataclass(frozen=True)
class Identity:
    """A loaded ed25519 identity (private key + derived public key)."""

    private_bytes: bytes  # 32-byte seed
    public_bytes: bytes  # 32-byte public key

    @property
    def agent_id(self) -> str:
        """Stable agent identifier derived from the public key.

        Format: ``agent_<16 hex chars>`` where the hex is the first
        8 bytes of ``sha256(public_bytes)``. Same input always
        produces the same output; different inputs collide with
        ~5 × 10⁻²⁰ probability for a million agents.
        """
        digest = hashlib.sha256(self.public_bytes).digest()
        return "agent_" + digest[:8].hex()

    @property
    def public_key_b64(self) -> str:
        """``ed25519:<base64>`` form, suitable for an Agent Card field."""
        return _PUB_PREFIX + base64.b64encode(self.public_bytes).decode("ascii")

    def sign(self, message: bytes) -> str:
        """Sign a message and return the signature as base64 (no prefix)."""
        sk = Ed25519PrivateKey.from_private_bytes(self.private_bytes)
        return base64.b64encode(sk.sign(message)).decode("ascii")

    @staticmethod
    def verify(public_b64: str, message: bytes, signature_b64: str) -> bool:
        """Verify a signature against a public key in ``ed25519:<b64>`` form."""
        if not public_b64.startswith(_PUB_PREFIX):
            return False
        try:
            pub = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(public_b64[len(_PUB_PREFIX):])
            )
            sig = base64.b64decode(signature_b64)
        except (ValueError, base64.binascii.Error, TypeError):
            return False
        try:
            pub.verify(sig, message)
            return True
        except Exception:
            return False


def _generate() -> Identity:
    """Generate a fresh ed25519 keypair."""
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(encoding=Encoding.Raw, format=PrivateFormat.Raw, encryption_algorithm=NoEncryption())
    pub = sk.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return Identity(private_bytes=priv, public_bytes=pub)


def _load_from_disk(path: Path) -> Optional[Identity]:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    try:
        priv = base64.b64decode(raw)
    except (ValueError, base64.binascii.Error):
        return None
    if len(priv) != 32:
        return None
    try:
        sk = Ed25519PrivateKey.from_private_bytes(priv)
        pub = sk.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    except (ValueError, TypeError):
        return None
    return Identity(private_bytes=priv, public_bytes=pub)


def _save_to_disk(path: Path, identity: Identity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(base64.b64encode(identity.private_bytes).decode("ascii") + "\n", encoding="ascii")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_or_create(path: Optional[Path] = None) -> Identity:
    """Load the existing identity, or generate + persist a new one."""
    p = path or _key_path()
    existing = _load_from_disk(p)
    if existing is not None:
        return existing
    fresh = _generate()
    _save_to_disk(p, fresh)
    return fresh


def public_key_b64(path: Optional[Path] = None) -> Optional[str]:
    """Return the agent's public key in ``ed25519:<b64>`` form, or None
    if no key has been generated yet. Does not generate a new key.
    """
    p = path or _key_path()
    ident = _load_from_disk(p)
    return ident.public_key_b64 if ident is not None else None


def agent_id(path: Optional[Path] = None) -> Optional[str]:
    """Return the canonical agentId for this agent, or None if no key exists.

    Use :func:`agent_id_or_url_fallback` if you want the v0.1.x URL-based
    fallback when no key is on disk.
    """
    p = path or _key_path()
    ident = _load_from_disk(p)
    return ident.agent_id if ident is not None else None


def agent_id_or_url_fallback(url: str, path: Optional[Path] = None) -> str:
    """Return the agentId, preferring the key but falling back to a URL hash.

    v0.2 path: key on disk → key-derived ID. Otherwise: v0.1.x URL hash.
    This is what makes v0.2 → v0.1.x interop work — meetings recorded
    by a v0.1.x peer keyed on the URL still resolve when the v0.2 peer
    upgrades, as long as the v0.1.x peer is still alive.
    """
    p = path or _key_path()
    ident = _load_from_disk(p)
    if ident is not None:
        return ident.agent_id
    from plugins.a2a_bridge.identity import agent_id_for

    return agent_id_for(url)
