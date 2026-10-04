"""Persisted meeting records.

When two agents agree to talk, both sides record the meeting at
``~/.hermes/a2a_bridge/meetings.json``. Each record captures:

  * the peer's agentId (key-derived in v0.2; URL-hash fallback for
    v0.1.x interop)
  * the peer's URL (for reachability — separate from identity, so a
    DNS change doesn't lose the meeting)
  * the peer's public key (for signature verification on subsequent
    envelopes)
  * the granted capabilities (what this peer is allowed to do with
    this side; e.g. ["read_public"], ["read_public", "write_public"])
  * the consent_until timestamp (TTL; after this the meeting is
    dormant until re-confirmed)
  * first_seen / last_seen for audit

The file is JSON. Concurrent writers should use the small helper
:meth:`Meetings.update` which writes atomically via a tmp + rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Meeting:
    agent_id: str
    peer_url: str
    peer_public_key: str
    granted: List[str] = field(default_factory=list)
    intent: str = ""
    established_at: str = ""
    consent_until: str = ""
    last_seen: str = ""
    revoked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Meeting":
        return cls(**{k: d.get(k, v) for k, v in cls.__dataclass_fields__.items() if k in d})

    def is_active(self, now: Optional[datetime] = None) -> bool:
        if self.revoked:
            return False
        if not self.consent_until:
            return False
        try:
            until = datetime.fromisoformat(self.consent_until.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (now or datetime.now(timezone.utc)) < until

    def can(self, capability: str) -> bool:
        return capability in self.granted


DEFAULT_TTL_DAYS = 30


def default_consent_until(ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _meetings_path() -> Path:
    """Resolve the meetings file path, with a graceful fallback."""
    try:
        from hermes_constants import get_hermes_home as _real
        base = _real()
    except Exception:
        base = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(base) / "a2a_bridge" / "meetings.json"


class Meetings:
    """In-memory + on-disk meeting store.

    Use as a context manager or instantiate and call :meth:`load` /
    :meth:`save` explicitly. All writes are atomic (tmp file +
    rename) so a crash mid-write can't corrupt the meetings file.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _meetings_path()
        self.by_agent_id: Dict[str, Meeting] = {}

    def load(self) -> "Meetings":
        if not self.path.exists():
            self.by_agent_id = {}
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.by_agent_id = {}
            return self
        if not isinstance(raw, dict):
            self.by_agent_id = {}
            return self
        meetings = raw.get("meetings", {}) or {}
        if not isinstance(meetings, dict):
            meetings = {}
        self.by_agent_id = {aid: Meeting.from_dict(m) for aid, m in meetings.items() if isinstance(m, dict)}
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "version": 1,
            "updated_at": _now_iso(),
            "meetings": {aid: m.to_dict() for aid, m in self.by_agent_id.items()},
        }
        # Atomic write.
        fd, tmp = tempfile.mkstemp(prefix="meetings.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, agent_id: str) -> Optional[Meeting]:
        return self.by_agent_id.get(agent_id)

    def all(self) -> List[Meeting]:
        return list(self.by_agent_id.values())

    def active(self, now: Optional[datetime] = None) -> List[Meeting]:
        return [m for m in self.by_agent_id.values() if m.is_active(now)]

    def upsert(self, meeting: Meeting) -> Meeting:
        if not meeting.established_at:
            meeting.established_at = _now_iso()
        meeting.last_seen = _now_iso()
        self.by_agent_id[meeting.agent_id] = meeting
        return meeting

    def revoke(self, agent_id: str) -> bool:
        m = self.by_agent_id.get(agent_id)
        if m is None:
            return False
        m.revoked = True
        m.last_seen = _now_iso()
        return True

    def touch(self, agent_id: str) -> None:
        m = self.by_agent_id.get(agent_id)
        if m is not None:
            m.last_seen = _now_iso()

    def list_for_display(self) -> str:
        if not self.by_agent_id:
            return "(no meetings yet — introduce yourself to a peer first)"
        lines = [
            f"{'agent_id':<22} {'status':<12} {'until':<22} {'granted':<24} peer_url",
            "-" * 100,
        ]
        now = datetime.now(timezone.utc)
        for m in sorted(self.by_agent_id.values(), key=lambda x: x.agent_id):
            status = "active" if m.is_active(now) else ("revoked" if m.revoked else "expired")
            granted = ",".join(m.granted) or "—"
            lines.append(
                f"{m.agent_id:<22} {status:<12} {m.consent_until:<22} {granted:<24} {m.peer_url}"
            )
        return "\n".join(lines)
