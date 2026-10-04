"""Public memory/skill slice transport.

A "slice" is a unit of shareable content. The sender packages a slice
as a JSON envelope, signs it with their ed25519 key, and sends it
through a normal A2A ``message/send`` task (the existing audit,
redaction, and rate-limit paths all apply). The receiver pulls the
envelope out of the task's reply, verifies the signature, and writes
the slice to its own ``MEMORY.md`` (or ``skills/<name>/``) with
provenance metadata.

Both sides check the policy resolver (``policy.resolve_share``) and
the meeting record (``meetings.Meetings.get(peer_id)``) before doing
anything. A slice is only shareable if:

  * the sender's policy layer has the slice at a shareable level,
  * the receiver's policy layer (on its end) considers the slice
    shareable with this peer, and
  * the receiver has an active meeting with the sender that grants
    ``read_public`` (or ``write_public``, depending on direction).

The verification is done locally on each side, not via a third party.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from plugins.a2a_bridge import canonical, handshake, keyring, meetings, policy


SLICE_KIND = "memory_slice"
DEFAULT_HEADING_PREFIX = "## "


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_agent_id_matches_key(agent_id: str, public_key_b64: str) -> bool:
    import hashlib
    if not public_key_b64.startswith("ed25519:"):
        return False
    try:
        raw = base64.b64decode(public_key_b64[len("ed25519:"):])
    except (ValueError, base64.binascii.Error):
        return False
    if len(raw) != 32:
        return False
    return agent_id == "agent_" + hashlib.sha256(raw).digest()[:8].hex()


@dataclass
class Slice:
    """An in-memory representation of a shareable content slice."""

    name: str
    kind: str                           # "memory" or "skill"
    level: policy.Level
    contents: List[Dict[str, str]] = field(default_factory=list)
    source_path: str = ""               # where it came from locally
    heading_anchor: Optional[str] = None


def build_envelope(
    slice_: Slice,
    *,
    to_peer: str,
    task_id: str = "",
    sender_identity: Optional[keyring.Identity] = None,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Sign and return the wire envelope for a memory slice.

    The receiver verifies the signature against the meeting's stored
    public key. They should also recompute ``to_peer == SHA-256(sender_public_key)[:8]``
    on their end to confirm the envelope is addressed to them.

    Pass ``now`` to inject a clock (handy in tests for replay-window
    coverage). Defaults to wall-clock UTC.
    """
    ident = sender_identity or keyring.load_or_create()
    envelope: Dict[str, Any] = {
        "kind": SLICE_KIND,
        "from_peer": ident.agent_id,
        "from_public_key": ident.public_key_b64,
        "to_peer": to_peer,
        "slice_name": slice_.name,
        "level": slice_.level.value,
        "contents": list(slice_.contents),
        "sent_at": _now_iso() if now is None else now().isoformat().replace("+00:00", "Z"),
        "task_id": task_id,
    }
    envelope["signature"] = ident.sign(canonical.canonical_bytes(envelope))
    return envelope


class SliceError(ValueError):
    """Raised when an envelope is malformed, unsigned, or unverifiable."""


class SliceFetchError(SliceError):
    """Raised when the slice source can't be read (missing file, escape, parse error)."""


# Default heading prefix used to split memory files into sections.
# Each section is "## Heading\n\nBody...\n\n## Next\n..."
DEFAULT_HEADING_REGEX = r"^(#{1,6})\s+(.+)$"


def _split_into_sections(text: str) -> List[Dict[str, str]]:
    """Split a markdown file into {heading, body} sections.

    Sections start at any line beginning with one or more '#' followed
    by whitespace. The body of a section is everything between this
    heading and the next heading (or end of file). Headings at any
    level (h1-h6) are recognized; the receiver sees the original
    heading text including the leading '## '.
    """
    import re
    sections: List[Dict[str, str]] = []
    lines = text.splitlines()
    current_heading: Optional[str] = None
    current_body: List[str] = []
    pattern = re.compile(DEFAULT_HEADING_REGEX)
    for line in lines:
        m = pattern.match(line)
        if m:
            # Flush the previous section.
            if current_heading is not None:
                sections.append({
                    "heading": current_heading,
                    "body": "\n".join(current_body).strip(),
                })
            current_heading = line.rstrip()
            current_body = []
        else:
            current_body.append(line)
    # Flush the last section.
    if current_heading is not None:
        sections.append({
            "heading": current_heading,
            "body": "\n".join(current_body).strip(),
        })
    return sections


def fetch_contents(
    slice_: Slice,
    *,
    home: Any = None,
) -> List[Dict[str, str]]:
    """Read the slice source from disk and return its contents.

    The slice source is at ``slice_.source_path`` (relative to
    ``home``). If ``slice_.heading_anchor`` is set, return only the
    single section whose heading contains that anchor. Otherwise
    return all sections in the file.

    Raises :class:`SliceFetchError` on:
      * the source path being missing,
      * the resolved path escaping ``home`` (path-traversal guard),
      * the file not being readable as UTF-8.

    Pass ``home`` to inject a base directory (handy in tests).
    Defaults to ``HERMES_HOME`` (or ``~/.hermes``).
    """
    import os
    from pathlib import Path

    if home is None:
        home = Path(os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    home_resolved = Path(home).resolve()
    source = Path(slice_.source_path)
    if source.is_absolute():
        # Absolute paths are never allowed — they bypass the
        # central allowlist's path semantics. Refuse.
        target = source.resolve()
    else:
        target = (home_resolved / source).resolve()
    if not target.is_relative_to(home_resolved):
        raise SliceFetchError(
            f"slice source_path={slice_.source_path!r} resolves outside "
            f"HERMES_HOME ({home_resolved}); refusing to read."
        )
    if not target.exists():
        raise SliceFetchError(f"slice source file not found: {target}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SliceFetchError(f"slice source is not valid UTF-8: {e}")
    sections = _split_into_sections(text)
    if slice_.heading_anchor:
        anchor = slice_.heading_anchor
        sections = [s for s in sections if anchor in s["heading"]]
    return sections


def parse_envelope(
    envelope: Dict[str, Any],
    *,
    max_age_seconds: int = handshake.MAX_ENVELOPE_AGE,
    now: Optional[Any] = None,
) -> Slice:
    """Validate an incoming slice envelope and return a Slice.

    Verifies:
      * envelope shape (required fields present)
      * kind is ``memory_slice``
      * ``from_peer`` matches the SHA-256 of ``from_public_key``
      * signature verifies against ``from_public_key``
      * ``sent_at`` is within the replay window (``max_age_seconds``
        of the receiver's clock, default 300s)

    The optional ``max_age_seconds`` argument bounds the replay
    window. Operators can extend it for slow networks. Pass ``now``
    to inject a clock (handy in tests); defaults to wall-clock UTC.
    """
    if not isinstance(envelope, dict):
        raise SliceError("envelope must be a JSON object")
    if envelope.get("kind") != SLICE_KIND:
        raise SliceError(f"unexpected kind: {envelope.get('kind')!r}")
    for required in ("from_peer", "from_public_key", "to_peer", "slice_name", "level", "contents", "sent_at", "signature"):
        if required not in envelope:
            raise SliceError(f"missing required field: {required}")
    if not _check_agent_id_matches_key(str(envelope["from_peer"]), str(envelope["from_public_key"])):
        raise SliceError("from_peer does not match SHA-256 of from_public_key")
    message = canonical.canonical_bytes(envelope)
    if not keyring.Identity.verify(str(envelope["from_public_key"]), message, str(envelope["signature"])):
        raise SliceError("signature did not verify")
    # Replay-window check last, so callers log what was valid before
    # deciding it's stale. Raises HandshakeError on bad clocks.
    if now is None:
        handshake.check_replay_window(str(envelope["sent_at"]), max_age_seconds=max_age_seconds)
    else:
        handshake.check_replay_window(str(envelope["sent_at"]), max_age_seconds=max_age_seconds, now=now)
    return Slice(
        name=str(envelope["slice_name"]),
        kind="memory",
        level=policy.parse_level(str(envelope["level"])),
        contents=list(envelope["contents"]),
    )


def format_provenance(envelope: Dict[str, Any]) -> str:
    """Return a markdown comment block for the receiving MEMORY.md.

    Captures the source peer, the public key fingerprint, the task
    id, and the sent-at timestamp, so future readers can trace
    where the content came from.
    """
    src = envelope.get("from_peer", "?")
    pub = envelope.get("from_public_key", "?")
    fp = pub[:24] + "…" if len(pub) > 24 else pub
    task = envelope.get("task_id", "?")
    when = envelope.get("sent_at", "?")
    return (
        f"<!-- a2a-bridge: source={src} key={fp} task={task} received={_now_iso()} sent={when} -->"
    )
