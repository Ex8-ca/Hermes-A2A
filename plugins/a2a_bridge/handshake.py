"""A2A meeting handshake — sign, verify, build, parse.

The handshake is a JSON-RPC message with method ``introduce`` (sent by
the initiator) or ``introduce_ack`` (sent by the responder). The
message body is a signed envelope:

  introduce:
    {
      "method": "introduce",
      "from": "<sender's agentId>",
      "agent_card_url": "<sender's A2A Agent Card URL>",
      "public_key": "ed25519:<base64>",
      "intent": "<free-form reason for the meeting>",
      "consent_until": "<ISO 8601>",
      "signature": "<base64 ed25519 over canonical JSON of all other fields>"
    }

  introduce_ack: same shape with an extra ``granted`` field listing
    the capabilities the responder is willing to give the initiator.

This module:
  * Builds outgoing envelopes, signing with the local identity.
  * Parses incoming envelopes, verifying the signature.
  * Checks that ``from`` matches the SHA-256 fingerprint of
    ``public_key`` (so an attacker can't claim someone else's ID).
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from plugins.a2a_bridge import canonical, identity as ident_v011
from plugins.a2a_bridge import keyring


def _check_agent_id_matches_key(agent_id: str, public_key_b64: str) -> bool:
    """Verify that ``agent_id`` is the SHA-256 fingerprint of ``public_key``."""
    if not public_key_b64.startswith("ed25519:"):
        return False
    try:
        raw = base64.b64decode(public_key_b64[len("ed25519:"):])
    except (ValueError, base64.binascii.Error):
        return False
    if len(raw) != 32:
        return False
    expected = "agent_" + hashlib.sha256(raw).digest()[:8].hex()
    return agent_id == expected


def build_introduce(
    *,
    agent_card_url: str,
    intent: str,
    consent_until: Optional[str] = None,
    ttl_days: int = 30,
    local_identity: Optional[keyring.Identity] = None,
) -> Dict[str, Any]:
    """Build + sign an outgoing introduce envelope.

    The local identity is loaded (or generated) on first use; pass
    ``local_identity`` to inject one (handy in tests).
    """
    ident = local_identity or keyring.load_or_create()
    if consent_until is None:
        consent_until = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")
    envelope: Dict[str, Any] = {
        "method": "introduce",
        "from": ident.agent_id,
        "agent_card_url": agent_card_url,
        "public_key": ident.public_key_b64,
        "intent": intent,
        "consent_until": consent_until,
    }
    envelope["signature"] = ident.sign(canonical.canonical_bytes(envelope))
    return envelope


def build_introduce_ack(
    *,
    agent_card_url: str,
    granted: List[str],
    consent_until: Optional[str] = None,
    ttl_days: int = 30,
    local_identity: Optional[keyring.Identity] = None,
) -> Dict[str, Any]:
    """Build + sign an outgoing introduce_ack envelope.

    ``granted`` is a list of capability strings the responder is
    willing to give the initiator, e.g. ``["read_public"]`` or
    ``["read_public", "write_public"]``. The responder's UI must
    have already gotten owner consent before this is sent.
    """
    ident = local_identity or keyring.load_or_create()
    if consent_until is None:
        consent_until = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")
    envelope: Dict[str, Any] = {
        "method": "introduce_ack",
        "from": ident.agent_id,
        "agent_card_url": agent_card_url,
        "public_key": ident.public_key_b64,
        "granted": list(granted),
        "consent_until": consent_until,
    }
    envelope["signature"] = ident.sign(canonical.canonical_bytes(envelope))
    return envelope


class HandshakeError(ValueError):
    """Raised when an envelope is malformed, unsigned, or unverifiable."""


def parse_incoming(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an incoming handshake envelope.

    Returns the envelope with the ``signature`` field removed (it has
    been verified). Raises :class:`HandshakeError` for any failure.
    """
    if not isinstance(envelope, dict):
        raise HandshakeError("envelope must be a JSON object")
    method = envelope.get("method")
    if method not in ("introduce", "introduce_ack"):
        raise HandshakeError(f"unexpected method: {method!r}")
    for required in ("from", "agent_card_url", "public_key", "consent_until", "signature"):
        if required not in envelope:
            raise HandshakeError(f"missing required field: {required}")
    agent_id = str(envelope["from"])
    public_key = str(envelope["public_key"])
    if not _check_agent_id_matches_key(agent_id, public_key):
        raise HandshakeError(
            f"from={agent_id!r} does not match the SHA-256 of public_key"
        )
    sig = str(envelope["signature"])
    message = canonical.canonical_bytes(envelope)
    if not keyring.Identity.verify(public_key, message, sig):
        raise HandshakeError("signature did not verify against public_key")
    # Strip the signature for the caller; they have a verified envelope.
    return {k: v for k, v in envelope.items() if k != "signature"}


def to_meeting_record(
    envelope: Dict[str, Any],
    *,
    our_url: str,
    our_granted_to_them: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convert a verified envelope into a meeting-record dict.

    The caller is responsible for having already run
    :func:`parse_incoming` (so the signature field is gone).
    """
    if envelope.get("method") not in ("introduce", "introduce_ack"):
        raise HandshakeError(f"unexpected method: {envelope.get('method')!r}")
    return {
        "agent_id": str(envelope["from"]),
        "peer_url": str(envelope["agent_card_url"]),
        "peer_public_key": str(envelope["public_key"]),
        "intent": str(envelope.get("intent", "")),
        "granted": list(our_granted_to_them or envelope.get("granted", []) or []),
        "consent_until": str(envelope["consent_until"]),
    }
