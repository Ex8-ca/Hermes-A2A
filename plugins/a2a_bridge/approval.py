"""Approval gate for a2a-bridge.

Inspects a task message and decides whether it needs explicit user
consent before being sent to a peer. Returns a structured payload that
the agent surfaces to the user (via the model's normal chat reply, or
the desktop's consent UI when present).

Sensitive categories detected:

  * memory_share — anything that looks like exporting a memory slice,
    persona file, or session notes to a peer.
  * credential_share — anything that looks like sending an API key,
    token, password, or SSH key.
  * config_write — anything that asks the peer to write to its own
    config.yaml, .env, MEMORY.md, or persona files.

The check is heuristic and *deliberately biases toward asking*. Better
to confirm an innocuous request than to leak a credential silently.

This module is pure functions; the handlers in tools.py call
:classify_task` to decide whether to send, ask, or refuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Word/phrase patterns. Case-insensitive. Each category is matched as a
# whole phrase, not as substrings inside random words — "remember" inside
# "remembering to" is fine, but "remember" alone is a memory_share flag.
#
# We err on the side of detecting — false positives just trigger an
# extra confirmation, false negatives can leak secrets.
_MEMORY_PATTERNS = (
    r"\bmemory\b",
    r"\bremember\b",
    r"\brecall\b",
    r"\bnotes?\s+(about|on|for)\b",
    r"\bMEMORY\.md\b",
    r"\bSOUL\.md\b",
    r"\bIDENTITY\.md\b",
    r"\bUSER\.md\b",
    r"\bpersona\b",
    r"\bsoul\b",
    r"\bdaily\s+logs?\b",
    r"\bheartbeat\b",
)
_CRED_PATTERNS = (
    r"\bapi[_ -]?key\b",
    r"\bbearer\s+token\b",
    r"\bpassword\b",
    r"\bpasswd\b",
    r"\bssh[_ -]?key\b",
    r"\bsecret\b",
    r"\bcredential\b",
    r"\benv\.?\s*var(?:iable)?\b",
    r"sk-[A-Za-z0-9]{16,}",          # OpenAI-style
    r"ghp_[A-Za-z0-9]{16,}",         # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}", # Slack token
    r"AIza[0-9A-Za-z_-]{20,}",       # Google API key
)
_CONFIG_WRITE_PATTERNS = (
    r"\bwrite\s+to\b.{0,40}\b(config|\.env|MEMORY|SOUL|IDENTITY|persona)\b",
    r"\bupdate\s+(your|the)\s+(config|persona|memory)\b",
    r"\bset\s+(A2A_|HERMES_)[A-Z_]+\b",
)


@dataclass(frozen=True)
class ApprovalRequest:
    """Returned when a task needs explicit user consent."""

    category: str          # one of: memory_share, credential_share, config_write
    matched_patterns: List[str]
    summary: str           # human-readable one-liner for the user
    preview: str           # first ~140 chars of the proposed message

    def to_user_block(self) -> str:
        """Render as a markdown block the agent pastes into its reply."""
        matches = ", ".join(f"`{m}`" for m in self.matched_patterns[:3])
        return (
            f"⚠️ **Approval needed** — this task would `{self.category}` with a peer.\n"
            f"\n"
            f"- Triggered by: {matches}\n"
            f"- Summary: {self.summary}\n"
            f"- Preview: \"{self.preview}…\"\n"
            f"\n"
            f"Confirm by calling `a2a_bridge_confirm` with the same `agent` and "
            f"`message`, plus `approved: true`. To cancel, pass `approved: false` "
            f"or just don't call `a2a_bridge_confirm`."
        )


def _scan(patterns: tuple, text: str) -> List[str]:
    out: List[str] = []
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            out.append(m.group(0))
    return out


def classify_task(message: str) -> ApprovalRequest | None:
    """Return an ApprovalRequest if the task is sensitive; None if it's safe to send.

    The check order is credential → config_write → memory_share, so the
    *most* sensitive hit is surfaced if several match.
    """
    if not message or not message.strip():
        return None

    cred = _scan(_CRED_PATTERNS, message)
    if cred:
        return ApprovalRequest(
            category="credential_share",
            matched_patterns=cred,
            summary="Looks like a credential, token, or API key would be sent.",
            preview=_preview(message),
        )

    cfg = _scan(_CONFIG_WRITE_PATTERNS, message)
    if cfg:
        return ApprovalRequest(
            category="config_write",
            matched_patterns=cfg,
            summary="Looks like it asks the peer to write to its own config / persona.",
            preview=_preview(message),
        )

    mem = _scan(_MEMORY_PATTERNS, message)
    if mem:
        return ApprovalRequest(
            category="memory_share",
            matched_patterns=mem,
            summary="Looks like a memory slice, persona file, or session notes would be shared.",
            preview=_preview(message),
        )

    return None


def _preview(message: str, n: int = 140) -> str:
    s = message.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# Self-test (run with: python -m plugins.a2a_bridge.approval)
if __name__ == "__main__":
    cases = [
        ("What's the weather on .3?", None),
        ("Please send my MEMORY.md to ai386.3", "memory_share"),
        ("Here's the API key: sk-XXX...1234 (synthetic)", "credential_share"),
        ("Update your config.yaml to add a new peer", "config_write"),
        ("Send my soul file", "memory_share"),
        ("Tell .3 hello and ask for its hostname", None),
    ]
    for text, expected in cases:
        r = classify_task(text)
        got = r.category if r else None
        flag = "OK" if got == expected else "FAIL"
        print(f"[{flag}] {text!r} -> {got} (expected {expected})")