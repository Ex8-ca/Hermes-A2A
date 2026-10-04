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
from typing import Any, Callable, Dict, List, Optional, Tuple

from plugins.a2a_bridge import canonical, identity as ident_v011
from plugins.a2a_bridge import keyring


#: Default max envelope age in seconds. Reject any incoming envelope whose
#: ``sent_at`` is older than ``now - MAX_ENVELOPE_AGE`` or more than
#: ``MAX_ENVELOPE_AGE`` in the future. This bounds the replay window.
MAX_ENVELOPE_AGE: int = 300


def _now_utc() -> datetime:
    """Wall-clock UTC. Replace with an injected clock in tests."""
    return datetime.now(timezone.utc)


def _now_iso(now_fn: Callable[[], datetime] = _now_utc) -> str:
    return now_fn().isoformat().replace("+00:00", "Z")


def check_replay_window(
    sent_at: str,
    *,
    max_age_seconds: int = MAX_ENVELOPE_AGE,
    now: Callable[[], datetime] = _now_utc,
) -> None:
    """Reject envelopes that are too old or too far in the future.

    Public helper so ``slice.py`` (and any future envelope type) can
    apply the same replay bound without duplicating the logic. Raises
    :class:`HandshakeError` on stale or future-dated envelopes.

    The default 300s window tolerates real-world clock skew but is
    short enough to bound the blast radius of a captured envelope.
    Operators can extend the window for slow networks via
    ``max_age_seconds``.
    """
    try:
        parsed = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HandshakeError(f"sent_at is not a valid ISO 8601 timestamp: {sent_at!r}")
    if parsed.tzinfo is None:
        # Naive datetimes are ambiguous; refuse rather than guess.
        raise HandshakeError(f"sent_at must be timezone-aware: {sent_at!r}")
    delta = now() - parsed
    if delta.total_seconds() > max_age_seconds:
        raise HandshakeError(
            f"envelope expired: sent_at={sent_at!r} is "
            f"{int(delta.total_seconds())}s old (max {max_age_seconds}s)"
        )
    if delta.total_seconds() < -max_age_seconds:
        raise HandshakeError(
            f"envelope not-yet-valid: sent_at={sent_at!r} is "
            f"{int(-delta.total_seconds())}s in the future (max {max_age_seconds}s)"
        )


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
    now: Callable[[], datetime] = _now_utc,
) -> Dict[str, Any]:
    """Build + sign an outgoing introduce envelope.

    The local identity is loaded (or generated) on first use; pass
    ``local_identity`` to inject one (handy in tests). Pass ``now``
    to inject a clock (handy for testing replay protection).
    """
    ident = local_identity or keyring.load_or_create()
    if consent_until is None:
        consent_until = (now() + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")
    envelope: Dict[str, Any] = {
        "method": "introduce",
        "from": ident.agent_id,
        "agent_card_url": agent_card_url,
        "public_key": ident.public_key_b64,
        "intent": intent,
        "sent_at": _now_iso(now),
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
    now: Callable[[], datetime] = _now_utc,
) -> Dict[str, Any]:
    """Build + sign an outgoing introduce_ack envelope.

    ``granted`` is a list of capability strings the responder is
    willing to give the initiator, e.g. ``["read_public"]`` or
    ``["read_public", "write_public"]``. The responder's UI must
    have already gotten owner consent before this is sent.
    """
    ident = local_identity or keyring.load_or_create()
    if consent_until is None:
        consent_until = (now() + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")
    envelope: Dict[str, Any] = {
        "method": "introduce_ack",
        "from": ident.agent_id,
        "agent_card_url": agent_card_url,
        "public_key": ident.public_key_b64,
        "granted": list(granted),
        "sent_at": _now_iso(now),
        "consent_until": consent_until,
    }
    envelope["signature"] = ident.sign(canonical.canonical_bytes(envelope))
    return envelope


class HandshakeError(ValueError):
    """Raised when an envelope is malformed, unsigned, or unverifiable."""


def parse_incoming(
    envelope: Dict[str, Any],
    *,
    max_age_seconds: int = MAX_ENVELOPE_AGE,
    now: Callable[[], datetime] = _now_utc,
) -> Dict[str, Any]:
    """Validate an incoming handshake envelope.

    Returns the envelope with the ``signature`` field removed (it has
    been verified). Raises :class:`HandshakeError` for any failure.

    The optional ``max_age_seconds`` argument bounds the replay
    window. Any envelope whose ``sent_at`` is older than
    ``now - max_age_seconds`` or more than ``max_age_seconds`` in
    the future is refused. The default 300s window tolerates
    real-world clock skew but is short enough to bound the
    blast radius of a captured envelope.

    Pass ``now`` to inject a clock (handy in tests).
    """
    if not isinstance(envelope, dict):
        raise HandshakeError("envelope must be a JSON object")
    method = envelope.get("method")
    if method not in ("introduce", "introduce_ack"):
        raise HandshakeError(f"unexpected method: {method!r}")
    for required in (
        "from",
        "agent_card_url",
        "public_key",
        "consent_until",
        "sent_at",
        "signature",
    ):
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
    # Replay-window check goes last so the caller can still log
    # *what* they would have accepted before deciding it's too old.
    check_replay_window(str(envelope["sent_at"]), max_age_seconds=max_age_seconds, now=now)
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
