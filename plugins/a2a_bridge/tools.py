"""Tool implementations for a2a-bridge.

Core tools (v0.1.x):

  a2a_bridge_send          — initiate a task. May return an approval request.
  a2a_bridge_confirm       — confirm (or cancel) a pending approval, then send.
  a2a_bridge_audit         — recent exchanges for a peer from the audit log.
  a2a_bridge_list_peers    — peers seen in the audit log (in addition to config).
  a2a_bridge_history       — recall a prior A2A conversation by context_id.
  a2a_bridge_shareable     — dry-run: report which declared slices are
                             shareable with a given peer.

v0.2 (signed public-share):

  a2a_bridge_introduce         — initiate a meeting with a peer (sends a
                                 signed "introduce" envelope).
  a2a_bridge_introduce_respond — handle an incoming introduce (the
                                 receiver's owner sees a consent prompt,
                                 then this tool sends a signed ack).
  a2a_bridge_meetings          — list active / expired / revoked meetings.
  a2a_bridge_revoke            — unilaterally revoke a meeting.
  a2a_bridge_share_public      — send a signed memory slice to a peer.
                                 Gated by both the central allowlist and
                                 the meeting record.
  a2a_bridge_receive_public    — handle an incoming memory slice (verify
                                 signature, write to local MEMORY.md with
                                 provenance).

`a2a_bridge_send` and `a2a_bridge_confirm` delegate to the underlying
Hermes A2A platform plugin's `a2a_call`. The platform plugin does the
real network call, auth, audit, persistence, redaction, and rate limit.

When the underlying a2a plugin is not enabled or has no peers, the tools
surface a structured error instead of crashing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugins.a2a_bridge import approval, audit, identity, policy  # noqa: F401
from plugins.a2a_bridge import handshake, keyring, meetings, slice as slice_mod  # noqa: F401
from plugins.a2a_bridge import memex8_client as memex8_client_mod  # noqa: F401

logger = logging.getLogger(__name__)

TOOLSET = "a2a_bridge"

# Populated by register() in __init__.py. Used by handlers to call back
# into the agent runtime's tool dispatcher when they need to invoke the
# underlying A2A plugin's tools.
_PLUGIN_CTX = None  # type: ignore[var-annotated]

# ─────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────

SCHEMA_SEND: Dict[str, Any] = {
    "name": "a2a_bridge_send",
    "description": (
        "Send a task to a peer A2A agent. Use a peer name from "
        "`a2a_bridge_list_peers` (or `a2a_list`) or a direct https URL. "
        "Tasks that match the approval gate (memory_share, credential_share, "
        "config_write) return a structured ApprovalRequest instead of sending; "
        "re-call with `a2a_bridge_confirm` after the user agrees."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "Configured peer name (e.g. 'ai386.3') or full https://... URL. Required."
                ),
            },
            "message": {
                "type": "string",
                "description": "One-paragraph task description. Required.",
            },
            "context_id": {
                "type": "string",
                "description": (
                    "Optional. Re-use a prior context_id to continue an existing conversation."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Optional per-call timeout in seconds (overrides peer default).",
                "minimum": 5,
                "maximum": 600,
            },
        },
        "required": ["agent", "message"],
    },
}

SCHEMA_CONFIRM: Dict[str, Any] = {
    "name": "a2a_bridge_confirm",
    "description": (
        "Confirm a previously-asked approval from `a2a_bridge_send`. Pass "
        "`approved: true` to actually send, or `approved: false` to cancel. "
        "After approval, the call behaves identically to `a2a_bridge_send`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Peer name or URL."},
            "message": {"type": "string", "description": "Task description."},
            "approved": {
                "type": "boolean",
                "description": "true = send now, false = cancel without sending.",
            },
            "context_id": {
                "type": "string",
                "description": "Optional context_id for continuation.",
            },
            "timeout": {"type": "integer", "minimum": 5, "maximum": 600},
        },
        "required": ["agent", "message", "approved"],
    },
}

SCHEMA_AUDIT: Dict[str, Any] = {
    "name": "a2a_bridge_audit",
    "description": (
        "Recent audit entries from ~/.hermes/a2a_audit.jsonl, optionally "
        "filtered by peer and direction. Useful when you need to see what "
        "happened on the wire without re-calling a peer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer": {
                "type": "string",
                "description": "Substring match on peer identifier (e.g. 'ai386.3', 'desktop', 'ip:127.0.0.1').",
            },
            "direction": {
                "type": "string",
                "enum": ["inbound", "outbound"],
                "description": "Filter by traffic direction. Omit for both.",
            },
            "last": {
                "type": "integer",
                "description": "How many recent entries to return. Default 20, max 200.",
                "minimum": 1,
                "maximum": 200,
                "default": 20,
            },
        },
    },
}

SCHEMA_LIST_PEERS: Dict[str, Any] = {
    "name": "a2a_bridge_list_peers",
    "description": (
        "List peers seen in the audit log (distinct 'peer' values, with the "
        "most recently active first). For the configured peers from "
        "config.yaml, call the underlying `a2a_list` instead — it includes "
        "auth/capabilities metadata."
    ),
    "parameters": {"type": "object", "properties": {}},
}

SCHEMA_HISTORY: Dict[str, Any] = {
    "name": "a2a_bridge_history",
    "description": (
        "Recall a previously persisted A2A conversation by its context_id. "
        "Returns the JSONL transcript that the underlying A2A plugin "
        "persisted under ~/.hermes/a2a_conversations/."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "context_id": {
                "type": "string",
                "description": "The context_id from a prior send/reply.",
            },
        },
        "required": ["context_id"],
    },
}


SCHEMA_SHAREABLE: Dict[str, Any] = {
    "name": "a2a_bridge_shareable",
    "description": (
        "Dry-run: list every slice in the central allowlist at "
        "~/.hermes/a2a_bridge/public.yaml and report whether each is "
        "shareable with a given peer. Combines the data-side frontmatter "
        "in the file with the policy-side allowlist (both must agree). "
        "Read-only — does not send or share anything. The actual share / "
        "request tools ship in v0.2 (see ROADMAP.md)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer_url": {
                "type": "string",
                "description": (
                    "Peer URL or agent_id to check against. The peer_id "
                    "used for resolution is the SHA-256 fingerprint of the "
                    "normalized URL (e.g. 'agent_8f3a7c2d9b1e4f5a')."
                ),
            },
        },
        "required": ["peer_url"],
    },
}


SCHEMA_INTRODUCE: Dict[str, Any] = {
    "name": "a2a_bridge_introduce",
    "description": (
        "Initiate a meeting with a peer A2A agent. Sends a signed "
        "'introduce' envelope (kind: introduce) carrying our agentId, "
        "public key, and a free-form intent. The peer's owner is asked "
        "to approve the meeting; on approval they send back a signed "
        "'introduce_ack' with the granted capabilities. Both sides "
        "persist the meeting at ~/.hermes/a2a_bridge/meetings.json. "
        "Re-introducing an already-met peer is safe and just refreshes "
        "last_seen."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer_url": {
                "type": "string",
                "description": (
                    "URL of the peer's A2A Agent Card. We fetch it to "
                    "discover the agent_id and public_key."
                ),
            },
            "intent": {
                "type": "string",
                "description": (
                    "Free-form reason for the meeting, e.g. 'share "
                    "memories and skills'. The peer sees this in the "
                    "consent prompt."
                ),
            },
            "ttl_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 30,
                "description": "Days until consent expires (default 30).",
            },
        },
        "required": ["peer_url", "intent"],
    },
}


SCHEMA_INTRODUCE_RESPOND: Dict[str, Any] = {
    "name": "a2a_bridge_introduce_respond",
    "description": (
        "Handle an incoming meeting request from a peer. The agent has "
        "already shown the user the peer's name, fingerprint, and intent. "
        "The user has approved with a set of granted capabilities. This "
        "tool signs and sends the introduce_ack, and persists the meeting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from_peer": {"type": "string", "description": "agentId of the initiator."},
            "agent_card_url": {"type": "string"},
            "from_public_key": {"type": "string"},
            "intent": {"type": "string"},
            "consent_until": {"type": "string"},
            "granted": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Capabilities to grant. E.g. ['read_public'] or ['read_public', 'write_public'].",
            },
            "approve": {
                "type": "boolean",
                "description": "true = send the ack and persist; false = decline.",
            },
        },
        "required": ["from_peer", "agent_card_url", "from_public_key", "intent", "consent_until", "granted", "approve"],
    },
}


SCHEMA_MEETINGS: Dict[str, Any] = {
    "name": "a2a_bridge_meetings",
    "description": (
        "List persisted meetings (active / expired / revoked) with their "
        "granted capabilities and consent windows. Read-only."
    ),
    "parameters": {"type": "object", "properties": {}},
}


SCHEMA_REVOKE: Dict[str, Any] = {
    "name": "a2a_bridge_revoke",
    "description": (
        "Unilaterally revoke a meeting. Stops accepting envelopes from "
        "the named agent. Persisted as revoked: true in meetings.json."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "description": "The peer's agentId to revoke."},
        },
        "required": ["agent_id"],
    },
}


SCHEMA_SHARE_PUBLIC: Dict[str, Any] = {
    "name": "a2a_bridge_share_public",
    "description": (
        "Send a public-marked memory slice to a peer. The slice is "
        "looked up in the central allowlist; its data-side frontmatter "
        "is read; both must agree the slice is shareable with this "
        "peer. If the peer is not yet on the met list, this tool "
        "returns a structured 'introduce first' response. The slice is "
        "signed with the local ed25519 key and sent as a normal A2A "
        "message; the receiver verifies the signature against the "
        "meeting's stored public key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer": {"type": "string", "description": "Peer name or URL (config-resolved) or full https URL."},
            "slice_name": {"type": "string", "description": "Slice name as declared in the allowlist."},
        },
        "required": ["peer", "slice_name"],
    },
}


SCHEMA_RECEIVE_PUBLIC: Dict[str, Any] = {
    "name": "a2a_bridge_receive_public",
    "description": (
        "Process an incoming memory slice envelope (typically from "
        "a2a_bridge_history). Verifies the signature, checks the "
        "allowlist on this side, then appends the slice's contents to "
        "the local MEMORY.md with a provenance comment. Returns the "
        "appended heading and the new MEMORY.md path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "envelope": {
                "type": "object",
                "description": "The signed slice envelope (kind=memory_slice).",
            },
            "write_to": {
                "type": "string",
                "default": "MEMORY.md",
                "description": "Filename under HERMES_HOME to write the slice into. Default MEMORY.md.",
            },
        },
        "required": ["envelope"],
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Underlying a2a plugin lookup
# ─────────────────────────────────────────────────────────────────────────

def _a2a_plugin_available() -> bool:
    """The A2A platform plugin must be enabled for any send/confirm to work.

    We check whether the runtime's tool registry actually has an
    ``a2a_call`` registered. That sidesteps the import dance with
    ``plugins.platforms.a2a.tools`` (which transitively imports
    ``gateway.platforms._shared`` and fails outside the agent runtime).

    When ``_PLUGIN_CTX`` is set (the real agent runtime called
    ``register(ctx)``), this is a live check. When running standalone,
    tests inject a fake ``_PLUGIN_CTX`` with a dispatch_tool().
    """
    if _PLUGIN_CTX is None:
        return False
    # If the test injected a fake ctx with `tools` dict, accept anything
    # in there as "available" — that's how we drive this without a real
    # Hermes agent tree on PYTHONPATH.
    fake_tools = getattr(_PLUGIN_CTX, "tools", None)
    if isinstance(fake_tools, dict):
        return bool(fake_tools)
    try:
        # Check the runtime registry, not the module import path.
        from tools.registry import registry  # type: ignore
        return registry.get_entry("a2a_call") is not None
    except Exception:
        return False


def _call_a2a_call(agent: str, message: str, context_id: str = "", timeout: int = 0) -> str:
    """Invoke the underlying a2a_call handler via the plugin runtime."""
    if _PLUGIN_CTX is None:
        return (
            "Error: a2a-bridge isn't loaded by the agent runtime yet. "
            "Start a fresh session — the bridge hooks into ctx on register()."
        )
    args: Dict[str, Any] = {"agent": agent, "message": message}
    if context_id:
        args["context_id"] = context_id
    if timeout > 0:
        args["timeout"] = timeout
    # Test-mode fast path: fake ctx exposes `tools` dict directly.
    fake_tools = getattr(_PLUGIN_CTX, "tools", None)
    if isinstance(fake_tools, dict):
        handler = fake_tools.get("a2a_call")
        if handler is None:
            return (
                "Error: the Hermes A2A platform plugin is not enabled. "
                "Run `hermes plugins enable a2a` and restart, then retry."
            )
        out = handler(args)
    else:
        try:
            out = _PLUGIN_CTX.dispatch_tool("a2a_call", args)
            # Missing tool -> dispatch returns {"error": "Unknown tool: ..."}
            if isinstance(out, dict) and "error" in out and "Unknown tool" in str(out.get("error", "")):
                return (
                    "Error: the Hermes A2A platform plugin is not enabled. "
                    "Run `hermes plugins enable a2a` and restart, then retry."
                )
        except Exception as e:  # pragma: no cover
            return f"Error: a2a_call raised {type(e).__name__}: {e}"
    # Normalize dict returns to a string for our handler contract.
    if isinstance(out, dict):
        return json.dumps(out, default=str)
    return out if isinstance(out, str) else str(out)


# v0.4.3 — peer-unreachable classifier.
#
# When the platform's a2a_call returns a string that looks like a network
# failure (urllib error, timeout, connection refused, etc.) and the peer URL
# is a Tailscale MagicDNS name or a private/LAN address, the generic
# "Error: call to X failed — ..." message doesn't tell the operator what
# they need to do. This helper substitutes a contextual message that names
# the specific network requirement (same tailnet, same LAN/VPN, etc.).
#
# Public for testability; no side effects.

_NETWORK_FAILURE_PATTERNS = (
    "urlopen error",
    "timed out",
    "Connection refused",
    "Connection reset",
    "Name or service not known",
    "No route to host",
    "Network is unreachable",
)


def _is_private_or_loopback_url(peer_url: str) -> str:
    """Classify a URL as 'tailscale-magicdns' / 'lan' / 'public'.

    Mirrors the directory's _validate.js isPrivateOrLoopbackHost
    (Tailscale ranges, RFC1918, link-local, IPv6 ULA, .ts.net suffix).

    Returns one of:
        "tailscale-magicdns" — host is *.ts.net
        "lan"               — host is RFC1918/loopback/link-local/Tailscale IP
        "public"            — anything else (or unparseable URL)
    """
    import ipaddress as _ip_address
    from urllib.parse import urlparse as _urlparse
    try:
        parsed = _urlparse(peer_url)
    except Exception:
        return "public"
    host = (parsed.hostname or "").lower()
    if not host:
        return "public"
    # Tailscale MagicDNS: e.g. ai5080.taila6e2e.ts.net
    if host.endswith(".ts.net") or host == "ts.net":
        return "tailscale-magicdns"
    # Try to parse as an IP address.
    try:
        ip = _ip_address.ip_address(host)
    except ValueError:
        return "public"
    if isinstance(ip, _ip_address.IPv4Address):
        # Tailscale CGNAT: 100.64/10 (Python stdlib doesn't know about it).
        if _ip_address.IPv4Address("100.64.0.0") <= ip <= _ip_address.IPv4Address("100.127.255.255"):
            return "lan"
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified:
            return "lan"
        return "public"
    # IPv6
    if ip.is_loopback or ip.is_link_local:
        return "lan"
    # ULA: fc00::/7 (covers fc00::/8 + fd00::/8). Python's is_private covers
    # this range but also documentation ranges like 2001:db8::/32, so we
    # exclude the documentation ranges first.
    if _ip_address.IPv6Address("2001:db8::") <= ip <= _ip_address.IPv6Address("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff"):
        return "public"
    if ip.is_private:
        return "lan"
    return "public"


def _classify_peer_unreachable(peer_url: str, raw_result: str) -> Optional[str]:
    """If raw_result looks like a network failure AND peer_url is on a
    Tailscale or LAN transport, return a contextual error message that
    tells the operator what to do. Otherwise return None and let the
    caller pass raw_result through unchanged.

    Network-failure patterns (substring match, any of these):
      "urlopen error", "timed out", "Connection refused",
      "Connection reset", "Name or service not known",
      "No route to host", "Network is unreachable"

    Auth errors (HTTP 401/403 / "rejected auth") are NOT overridden —
    they have a different fix path and the platform's message is more
    useful than ours.
    """
    if not raw_result or not peer_url:
        return None
    if "rejected auth" in raw_result or "HTTP 401" in raw_result or "HTTP 403" in raw_result:
        return None
    if not any(pat in raw_result for pat in _NETWORK_FAILURE_PATTERNS):
        return None
    transport = _is_private_or_loopback_url(peer_url)
    if transport == "public":
        return None
    # Display the URL as the operator typed it (no normalization).
    if transport == "tailscale-magicdns":
        return (
            f"Error: peer is on a Tailscale tailnet (URL: {peer_url}). "
            "Your host is not on that tailnet, so the A2A call could not "
            "reach it. Run `tailscale status` to check your tailnet membership, "
            "or ask the peer operator to add your host to the tailnet's ACL. "
            f"(Original error: {raw_result})"
        )
    # transport == "lan"
    return (
        f"Error: peer is on a private network (URL: {peer_url}; "
        "RFC1918 / loopback / link-local / Tailscale CGNAT). Your host is "
        "not on the same network, so the A2A call could not reach it. "
        "Confirm your machine is on the same LAN, VPN, or Tailscale "
        f"tailnet as the peer. (Original error: {raw_result})"
    )


def _call_a2a_history(context_id: str) -> str:
    if _PLUGIN_CTX is None:
        return (
            "Error: a2a-bridge isn't loaded by the agent runtime yet. "
            "Start a fresh session — the bridge hooks into ctx on register()."
        )
    fake_tools = getattr(_PLUGIN_CTX, "tools", None)
    if isinstance(fake_tools, dict):
        handler = fake_tools.get("a2a_history")
        if handler is None:
            return "Error: the Hermes A2A platform plugin is not enabled."
        out = handler({"context_id": context_id})
    else:
        try:
            out = _PLUGIN_CTX.dispatch_tool("a2a_history", {"context_id": context_id})
            if isinstance(out, dict) and "error" in out and "Unknown tool" in str(out.get("error", "")):
                return "Error: the Hermes A2A platform plugin is not enabled."
        except Exception as e:  # pragma: no cover
            return f"Error: a2a_history raised {type(e).__name__}: {e}"
    if isinstance(out, dict):
        return json.dumps(out, default=str)
    return out if isinstance(out, str) else str(out)


# ─────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────

def handle_send(args: Dict[str, Any], **_kw) -> str:
    agent = (args.get("agent") or "").strip()
    message = (args.get("message") or "").strip()
    if not agent or not message:
        return "Error: both 'agent' and 'message' are required."

    approval_request = approval.classify_task(message)
    if approval_request is not None:
        # We do NOT fire the call. Return the approval block; the agent
        # shows it to the user. To proceed, the model (or the user via
        # the desktop consent UI) calls a2a_bridge_confirm.
        return approval_request.to_user_block()

    if not _a2a_plugin_available():
        return (
            "Error: the Hermes A2A platform plugin is not enabled. "
            "Run `hermes plugins enable a2a` and restart, then retry."
        )

    t0 = time.time()
    reply = _call_a2a_call(
        agent=agent,
        message=message,
        context_id=(args.get("context_id") or "").strip(),
        timeout=int(args.get("timeout") or 0),
    )
    # v0.4.3: surface a contextual "peer unreachable" error when the URL is
    # a Tailscale MagicDNS name or a private/LAN address and the platform
    # returned a generic network-error string.
    classified = _classify_peer_unreachable(agent, reply)
    if classified is not None:
        reply = classified
    elapsed_ms = int((time.time() - t0) * 1000)
    return _format_reply(reply, elapsed_ms=elapsed_ms, label="send")


def handle_confirm(args: Dict[str, Any], **_kw) -> str:
    agent = (args.get("agent") or "").strip()
    message = (args.get("message") or "").strip()
    approved = bool(args.get("approved"))
    if not agent or not message:
        return "Error: both 'agent' and 'message' are required."
    if not approved:
        return f"Cancelled. The task to '{agent}' was not sent."

    if not _a2a_plugin_available():
        return (
            "Error: the Hermes A2A platform plugin is not enabled. "
            "Run `hermes plugins enable a2a` and restart, then retry."
        )

    t0 = time.time()
    reply = _call_a2a_call(
        agent=agent,
        message=message,
        context_id=(args.get("context_id") or "").strip(),
        timeout=int(args.get("timeout") or 0),
    )
    # v0.4.3: surface a contextual "peer unreachable" error when the URL is
    # a Tailscale MagicDNS name or a private/LAN address.
    classified = _classify_peer_unreachable(agent, reply)
    if classified is not None:
        reply = classified
    elapsed_ms = int((time.time() - t0) * 1000)
    header = f"✅ approved + sent in {elapsed_ms} ms"
    return f"{header}\n{_format_reply(reply, elapsed_ms=elapsed_ms, label='confirm')}"


def handle_audit(args: Dict[str, Any], **_kw) -> str:
    rows = audit.read_audit(
        peer=(args.get("peer") or None),
        direction=(args.get("direction") or None),
        last=int(args.get("last") or 20),
    )
    return audit.format_audit_table(rows)


def handle_list_peers(args: Dict[str, Any] | None = None, **_kw) -> str:
    rows = audit.list_peers()
    return audit.format_peer_list(rows)


def handle_history(args: Dict[str, Any], **_kw) -> str:
    context_id = (args.get("context_id") or "").strip()
    if not context_id:
        return "Error: 'context_id' is required."
    return _call_a2a_history(context_id)


def handle_shareable(args: Dict[str, Any], **_kw) -> str:
    """Dry-run: report which slices are shareable with the given peer.

    Resolves the peer via the identity primitive, loads the central
    allowlist, and for every declared slice reports:
      * the slice name
      * its declared level (public_all / public_approved / deny)
      * whether the data-side frontmatter agrees
      * the final shareable-with-this-peer verdict
    """
    raw = (args.get("peer_url") or "").strip()
    if not raw:
        return "Error: 'peer_url' is required."
    try:
        if raw.startswith("agent_"):
            peer_id = raw
        else:
            peer_id = identity.agent_id_for(raw)
    except ValueError as e:
        return f"Error: invalid peer URL: {e}"

    al = policy.load_allowlist()
    if al.diagnostics:
        diag = "\n".join(f"  - {d}" for d in al.diagnostics)
        return f"Allowlist at {al.path} has diagnostics:\n{diag}"

    if not al.slices:
        return (
            f"No slices declared in {al.path}. Add memory/skill entries "
            f"under `slices.memories` and `slices.skills` to begin marking "
            f"content as public. See ROADMAP.md for the format."
        )

    rows: List[Tuple[str, str, str, str, str]] = []
    for slice_, fm, shareable, reason in policy.list_shareable(
        al, peer_id=peer_id, memories=[], skills=[]
    ):
        fm_marker = "—" if fm is None else fm.source
        fm_level = "—" if fm is None else fm.level.value
        verdict = "✓ shareable" if shareable else "✗ blocked"
        rows.append((slice_.name, slice_.kind, slice_.level.value, f"{fm_marker}/{fm_level}", f"{verdict}: {reason}"))

    headers = ("slice", "kind", "level", "frontmatter", "verdict")
    widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    def _fmt(row: Tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(row))
    lines = [_fmt(headers), "  ".join("-" * w for w in widths)]
    for r in rows:
        lines.append(_fmt(r))
    return f"Shareable-with-{peer_id} (dry-run; no actual sharing):\n\n" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# v0.2 — meeting + share handlers
# ─────────────────────────────────────────────────────────────────────────


def _resolve_peer_entry(agent: str) -> Optional[Dict[str, Any]]:
    """Resolve a peer name or URL through the underlying a2a tool's resolver."""
    try:
        return policy_module_resolve_peer(agent)
    except Exception:
        return None


# Tiny indirection: keep the resolve logic in tools.py so it can use the
# plugin's runtime context. The actual resolver lives in the a2a
# platform plugin's tools.py; we re-implement the smallest viable
# version here to avoid a hard import.
def policy_module_resolve_peer(agent: str) -> Optional[Dict[str, Any]]:
    """Mirror of plugins.a2a.tools._resolve_peer for our local use."""
    # Try to load the actual one from the a2a platform plugin; if it
    # isn't available, fall back to a URL-only resolver.
    try:
        from plugins.platforms.a2a import tools as a2a_tools  # type: ignore

        return a2a_tools._resolve_peer(agent)  # type: ignore[attr-defined]
    except Exception:
        # URL-only fallback: treat agent as a URL.
        if agent.startswith(("http://", "https://")):
            return {"url": agent, "auth": {}, "timeout": 120, "capabilities": []}
        return None


def handle_introduce(args: Dict[str, Any], **_kw) -> str:
    """Initiate a meeting by sending a signed 'introduce' envelope.

    We do not actually call the peer here — the meeting is a
    JSON-RPC message that the peer's a2a-bridge plugin will handle.
    We build + sign the envelope, send it through a2a_call, and
    return a structured reply that tells the user to wait for the
    peer's introduce_ack to arrive (typically via a2a_bridge_history
    or a webhook the plugin provides).
    """
    peer_url = (args.get("peer_url") or "").strip()
    intent = (args.get("intent") or "").strip()
    if not peer_url or not intent:
        return "Error: 'peer_url' and 'intent' are required."
    ttl_days = int(args.get("ttl_days") or 30)

    if not _a2a_plugin_available():
        return ("Error: the Hermes A2A platform plugin is not enabled. "
                "Run `hermes plugins enable a2a` and restart, then retry.")

    envelope = handshake.build_introduce(
        agent_card_url=peer_url,
        intent=intent,
        ttl_days=ttl_days,
    )
    # The actual transport: ask a2a_call to deliver a structured
    # "introduce" request. a2a_call passes our envelope as the
    # message text. The receiver's a2a-bridge plugin recognizes
    # kind=introduce and routes it accordingly.
    msg = json.dumps({"envelope": envelope, "type": "introduce"})
    # Security: route through the same approval gate as a2a_bridge_send.
    # The intent text is user-controlled and may contain memory-slice
    # payloads, credentials, or config-write instructions that should
    # not be silently shipped off — confirm with the user first.
    approval_request = approval.classify_task(msg)
    if approval_request is not None:
        return approval_request.to_user_block()
    reply = _call_a2a_call(agent=peer_url, message=msg)
    # v0.4.3: surface a contextual "peer unreachable" error when the URL is
    # a Tailscale MagicDNS name or a private/LAN address.
    classified = _classify_peer_unreachable(peer_url, reply)
    if classified is not None:
        reply = classified
    return (
        f"introduce sent to {peer_url}\n"
        f"  our agent_id:    {envelope['from']}\n"
        f"  intent:          {envelope['intent']}\n"
        f"  consent_until:   {envelope['consent_until']}\n"
        f"  peer reply:      {reply}"
    )


def handle_introduce_respond(args: Dict[str, Any], **_kw) -> str:
    """Handle the receiver side of a meeting.

    The agent has already shown the user the request and gotten
    approval (or denial). If approved, build + sign the ack and
    persist the meeting on this side.
    """
    if not bool(args.get("approve")):
        return f"Meeting declined. No record persisted."

    # Reconstruct the introduce envelope the user already saw, and
    # add the `signature` field from the args. (The agent is
    # responsible for surfacing a structured consent prompt with
    # these fields and the user already approved. The signature
    # check is performed by ``parse_incoming``.)
    incoming = {
        "method": "introduce",
        "from": args.get("from_peer", ""),
        "agent_card_url": args.get("agent_card_url", ""),
        "public_key": args.get("from_public_key", ""),
        "intent": args.get("intent", ""),
        "consent_until": args.get("consent_until", ""),
    }
    # ``sent_at`` is required for the replay-window check. The agent
    # passes it through from the original envelope.
    if "sent_at" in args:
        incoming["sent_at"] = args["sent_at"]
    # We need the signature. If the user only approved the consent,
    # they probably don't have it. The agent's UI must show it
    # alongside the consent prompt; the agent passes it through.
    if "signature" in args:
        incoming["signature"] = args["signature"]

    try:
        verified = handshake.parse_incoming(incoming)
    except handshake.HandshakeError as e:
        return f"Error: incoming envelope failed verification: {e}"

    # Cross-check: if we already have a meeting record for this
    # agentId, the public key on this envelope must match. Defense
    # in depth against a future bug in the agentId/key fingerprint
    # check, or a key-collision attack.
    store = meetings.Meetings().load()
    existing = store.get(verified["from"])
    if existing is not None and existing.peer_public_key != verified["public_key"]:
        return (
            f"Error: incoming envelope's public_key does not match "
            f"the public key on file for {verified['from']}; refusing "
            f"to rebind the meeting. (This is defense in depth — the "
            f"agentId/key fingerprint check in parse_incoming should "
            f"have already caught this.)"
        )

    if not _a2a_plugin_available():
        return ("Error: the Hermes A2A platform plugin is not enabled; "
                "cannot send the ack.")

    granted = list(args.get("granted") or [])
    ack = handshake.build_introduce_ack(
        agent_card_url=_our_card_url(),
        granted=granted,
        consent_until=verified["consent_until"],
    )
    msg = json.dumps({"envelope": ack, "type": "introduce_ack"})
    reply = _call_a2a_call(agent=verified["agent_card_url"], message=msg)
    # v0.4.3: surface a contextual "peer unreachable" error when the URL is
    # a Tailscale MagicDNS name or a private/LAN address.
    classified = _classify_peer_unreachable(verified["agent_card_url"], reply)
    if classified is not None:
        reply = classified

    # Persist the meeting on our side.
    record = handshake.to_meeting_record(verified, our_url=_our_card_url(), our_granted_to_them=granted)
    store.upsert(meetings.Meeting(**record))
    store.save()
    return f"Meeting persisted. Sent ack. Peer reply: {reply}"


def handle_meetings(args: Dict[str, Any], **_kw) -> str:
    return meetings.Meetings().load().list_for_display()


def handle_revoke(args: Dict[str, Any], **_kw) -> str:
    aid = (args.get("agent_id") or "").strip()
    if not aid:
        return "Error: 'agent_id' is required."
    store = meetings.Meetings().load()
    if store.revoke(aid):
        store.save()
        return f"Meeting with {aid} revoked."
    return f"No meeting with {aid} to revoke."


def handle_share_public(args: Dict[str, Any], **_kw) -> str:
    peer = (args.get("peer") or "").strip()
    slice_name = (args.get("slice_name") or "").strip()
    if not peer or not slice_name:
        return "Error: 'peer' and 'slice_name' are required."

    al = policy.load_allowlist()
    slice_ = al.slice_by_name(slice_name)
    if slice_ is None:
        return f"Error: slice '{slice_name}' is not in the allowlist at {al.path}."
    if not slice_.level.is_shareable():
        return f"Error: slice '{slice_name}' is {slice_.level.value} in the allowlist; not shareable."

    # Resolve the peer's id for the policy check.
    peer_entry = _resolve_peer_entry(peer)
    if peer_entry is None:
        return f"Error: unknown peer '{peer}'. Configure it under a2a_agents in config.yaml or pass a full https URL."
    # We don't have the peer's agentId until we've met; fall back to
    # the URL-derived id for the policy check.
    try:
        peer_id = identity.agent_id_for(peer_entry["url"])
    except ValueError as e:
        return f"Error: invalid peer URL: {e}"

    shareable, reason = policy.resolve_share(al, peer_id=peer_id, slice_name=slice_name)
    if not shareable:
        return f"Error: slice '{slice_name}' is not shareable with {peer_id}: {reason}"

    if not _a2a_plugin_available():
        return ("Error: the Hermes A2A platform plugin is not enabled; "
                "cannot send the slice.")

    # Build a real slice. v0.3 reads the actual heading + body
    # from the source file at slice_.path, filtered to the
    # heading_anchor if set. The fetched content goes into the
    # envelope; the receiver sees the same bytes the sender had.
    try:
        home = Path(os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
        contents = slice_mod.fetch_contents(
            slice_, home=home,
        )
    except slice_mod.SliceFetchError as e:
        return f"Error: failed to fetch slice contents: {e}"
    if not contents:
        return (
            f"Error: slice '{slice_name}' resolved to no sections "
            f"from {slice_.path} (anchor={slice_.heading_anchor!r}); "
            f"refusing to send an empty slice."
        )
    s = slice_mod.Slice(
        name=slice_name,
        kind=slice_.kind,
        level=slice_.level,
        contents=contents,
    )
    envelope = slice_mod.build_envelope(s, to_peer=peer_id)
    msg = json.dumps({"envelope": envelope, "type": "memory_slice"})
    # Security: route through the same approval gate as a2a_bridge_send.
    # The slice contents are user/data-controlled and may carry
    # credentials, persona fragments, or other sensitive material; the
    # central allowlist says the *level* is shareable, but the *body*
    # still needs the owner's explicit go-ahead.
    approval_request = approval.classify_task(msg)
    if approval_request is not None:
        return approval_request.to_user_block()
    reply = _call_a2a_call(agent=peer, message=msg)
    # v0.4.3: surface a contextual "peer unreachable" error when the URL is
    # a Tailscale MagicDNS name or a private/LAN address.
    classified = _classify_peer_unreachable(peer, reply)
    if classified is not None:
        reply = classified
    return f"slice '{slice_name}' sent to {peer} (kind=memory_slice, level={slice_.level.value}); peer reply: {reply}"


def handle_receive_public(args: Dict[str, Any], **_kw) -> str:
    envelope = args.get("envelope")
    if not isinstance(envelope, dict):
        return "Error: 'envelope' must be a JSON object."
    write_to = (args.get("write_to") or "MEMORY.md").strip()

    try:
        slice_ = slice_mod.parse_envelope(envelope)
    except slice_mod.SliceError as e:
        return f"Error: incoming slice failed verification: {e}"

    # Policy check on this side: would we share this slice with the
    # sender? The signature is valid; the question is whether the
    # *content* is allowed.
    sender_id = str(envelope.get("from_peer", ""))
    al = policy.load_allowlist()
    own = al.slice_by_name(slice_.name)
    if own is None:
        # Allow on the receiver's side if the central allowlist has
        # it at public_all (we don't know what the sender's policy
        # is, but our own gates it).
        if slice_.level != policy.Level.PUBLIC_ALL:
            return (
                f"Refused: slice '{slice_.name}' is not in our central "
                f"allowlist, and its level is {slice_.level.value}; "
                f"refusing to write."
            )
    else:
        shareable, reason = policy.resolve_share(al, peer_id=sender_id, slice_name=slice_.name)
        if not shareable:
            return f"Refused: our policy blocks slice '{slice_.name}' from {sender_id}: {reason}"

    # Check meeting record: we must have an active meeting with the
    # sender that grants read_public. Cross-check: the public key on
    # the envelope must match the meeting record's stored public key.
    store = meetings.Meetings().load()
    m = store.get(sender_id)
    if m is None or not m.is_active() or not m.can("read_public"):
        return f"Refused: no active meeting with {sender_id} that grants read_public."
    envelope_public_key = str(envelope.get("from_public_key", ""))
    if m.peer_public_key != envelope_public_key:
        return (
            f"Refused: incoming slice's from_public_key does not match "
            f"the public key on file for {sender_id}; refusing the slice. "
            f"(Defense in depth — the agentId/key fingerprint check in "
            f"parse_envelope should have already caught this.)"
        )

    # Append to MEMORY.md with provenance.
    prov = slice_mod.format_provenance(envelope)
    body_parts = [prov, ""]
    for entry in slice_.contents:
        body_parts.append(entry.get("heading", ""))
        body_parts.append("")
        body_parts.append(entry.get("body", ""))
        body_parts.append("")
    block = "\n".join(body_parts)

    home = Path(os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    # Security: resolve write_to against HERMES_HOME and refuse any path
    # that escapes it. A caller (or a prompt-injected envelope) could
    # otherwise pass write_to='../../../tmp/payload' and have us write
    # wherever they like. Resolve both sides and check containment.
    home_resolved = home.resolve()
    target = (home / write_to).resolve()
    if not target.is_relative_to(home_resolved):
        return (
            f"Error: write_to={write_to!r} resolves outside HERMES_HOME "
            f"({home_resolved}); refused."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write("\n" + block + "\n")
    store.touch(sender_id)
    store.save()
    return f"slice '{slice_.name}' from {sender_id} appended to {target} (with provenance)."


def _our_card_url() -> str:
    """Best-effort: read the local A2A_HOST/PORT from .env or fall back to localhost."""
    home = Path(os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    env_path = home / ".env"
    host = "127.0.0.1"
    port = 9900
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("A2A_HOST="):
                host = line.split("=", 1)[1].strip()
            elif line.startswith("A2A_PORT="):
                try:
                    port = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
    return f"http://{host}:{port}"


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _format_reply(raw_reply: str, elapsed_ms: int, label: str) -> str:
    """Pass-through formatting. The a2a plugin already returns a tidy
    '[peer · context ctx-... · completed]' header; we add an elapsed_ms
    hint for the model and surface the body unchanged.
    """
    if raw_reply.startswith("Error"):
        return raw_reply
    return f"{raw_reply}\n(elapsed: {elapsed_ms} ms via a2a_bridge.{label})"


# ─────────────────────────────────────────────────────────────────────────
# memex8-backed slice tools (sender, receiver, discovery)
# ─────────────────────────────────────────────────────────────────────────
#
# Three new tools added in feature/memex8-slice. See slice.py for the
# envelope format and policy.py for the per-peer allowlist semantics.
#
# Sender:
#   a2a_bridge_share_memex8(peer, slice_name, memory_ids)
#     Verifies each memory is visibility=public locally, then signs and
#     sends a memex8_memory_slice envelope.
# Discovery:
#   a2a_bridge_list_memex8_public(limit=20, offset=0)
#     Calls GET /api/v1/memories/public and returns a printable summary
#     so the agent can see what's shareable before sending.
# Receiver:
#   a2a_bridge_receive_memex8(envelope, write_to='MEMORY.md')
#     Verifies the envelope signature, re-fetches each memory from
#     the sender's memex8, re-checks visibility, then appends to
#     write_to with a provenance comment.


SCHEMA_LIST_MEMEX8_PUBLIC: Dict[str, Any] = {
    "name": "a2a_bridge_list_memex8_public",
    "description": (
        "List memex8 memories marked visibility=public. Use this before "
        "calling a2a_bridge_share_memex8 to discover what is shareable. "
        "Reads MEMEX8_API_BASE / MEMEX8_API_KEY from the environment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 20,
                "minimum": 1,
                "maximum": 200,
                "description": "Max memories to return (default 20, max 200).",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "minimum": 0,
                "description": "Pagination offset.",
            },
        },
    },
}


def handle_list_memex8_public(args: Dict[str, Any], **_kw) -> str:
    limit = int(args.get("limit") or 20)
    offset = int(args.get("offset") or 0)
    if limit < 1 or limit > 200:
        return "Error: limit must be 1..200"
    if offset < 0:
        return "Error: offset must be >= 0"
    try:
        client = memex8_client_mod.Memex8Client()
        data = client.list_public(limit=limit, offset=offset)
    except memex8_client_mod.Memex8ClientError as e:
        return f"Error: memex8 unreachable: {e}"
    memories = data.get("memories") or []
    total = data.get("total", len(memories))
    if not memories:
        return "No public memex8 memories found."
    lines = [
        f"memex8 public memories (showing {len(memories)} of {total}):",
        "",
    ]
    for m in memories:
        mid = m.get("id", "?")
        heading = (m.get("heading") or m.get("realm_name") or "").strip()
        preview = (m.get("content_preview") or "").strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        head = f"  - [{mid}] {heading}" if heading else f"  - [{mid}]"
        lines.append(head)
        if preview:
            lines.append(f"      {preview}")
    return "\n".join(lines).rstrip()


SCHEMA_SHARE_MEMEX8: Dict[str, Any] = {
    "name": "a2a_bridge_share_memex8",
    "description": (
        "Send a set of public memex8 memories to a peer as a signed "
        "memex8_memory_slice envelope. Every memory_id must be "
        "visibility=public locally; private memories are refused. The "
        "peer must be in the central allowlist's approved_peers and "
        "have an active meeting that grants read_public. The receiver "
        "re-fetches each memory from MEMEX8_API_BASE and re-checks "
        "visibility before writing, so a memory that was public at "
        "sign time but flipped to private before delivery is refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer": {
                "type": "string",
                "description": "Peer name (as configured in a2a_agents) or full URL.",
            },
            "slice_name": {
                "type": "string",
                "description": "Friendly handle for this slice (used in audit log + UI).",
            },
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of memex8 memory IDs to include.",
                "minItems": 1,
            },
        },
        "required": ["peer", "slice_name", "memory_ids"],
    },
}


def handle_share_memex8(args: Dict[str, Any], **_kw) -> str:
    peer = (args.get("peer") or "").strip()
    slice_name = (args.get("slice_name") or "").strip()
    memory_ids = args.get("memory_ids") or []
    if not peer:
        return "Error: 'peer' is required."
    if not slice_name:
        return "Error: 'slice_name' is required."
    if not isinstance(memory_ids, list) or not memory_ids:
        return "Error: 'memory_ids' must be a non-empty list."
    if not all(isinstance(m, str) and m.strip() for m in memory_ids):
        return "Error: 'memory_ids' must contain only non-empty strings."

    # Sender-side preflight: every requested memory must exist and
    # be visibility=public on our local memex8. We do NOT skip this
    # even though the receiver re-checks: a failure here produces a
    # much clearer error than the receiver's network round-trip.
    try:
        client = memex8_client_mod.Memex8Client()
        for mid in memory_ids:
            mem = client.get_memory(mid)
            visibility = (mem.get("visibility") or "").strip()
            if visibility != "public":
                return (
                    f"Error: memory {mid!r} is visibility={visibility!r} on "
                    f"our memex8; refusing to share a non-public memory."
                )
    except memex8_client_mod.Memex8ClientError as e:
        return f"Error: memex8 preflight failed: {e}"

    # Resolve peer + check allowlist (same gate the filesystem
    # share_public uses; the per-peer allowlist is the single source
    # of truth for who we share with, regardless of slice kind).
    peer_entry = _resolve_peer_entry(peer)
    if peer_entry is None:
        return f"Error: unknown peer '{peer}'."
    try:
        peer_id = identity.agent_id_for(peer_entry["url"])
    except ValueError as e:
        return f"Error: invalid peer URL: {e}"
    al = policy.load_allowlist()
    # The memex8 slice isn't a "slice" in the allowlist's sense
    # (filesystem + frontmatter). We treat it as public_all when the
    # peer is in the allowlist's approved_peers (or approved_peers is
    # empty and the meet layer is the gate).
    slice_ = slice_mod.Slice(
        name=slice_name,
        kind="memory",
        level=policy.Level.PUBLIC_ALL,
    )
    # The allowlist's slice_by_name is a registry of declared
    # filesystem slices; a memex8 slice name won't be there. So we
    # construct a synthetic Allowlist-shaped check by gating on
    # approved_peers directly.
    if al.approved_peers and peer_id not in al.approved_peers:
        return (
            f"Error: peer {peer_id!r} is not in the central allowlist's "
            f"approved_peers; refusing to share a memex8 slice."
        )
    # The allowlist's resolve_share expects a registered slice; for
    # the memex8 slice we use the same function as a sanity check on
    # the level=public_all path.
    shareable, reason = policy.resolve_share(al, peer_id=peer_id, slice_name=slice_name)
    # If the slice isn't in the allowlist, resolve_share returns
    # False with a clear reason. We treat that as a soft miss and
    # fall back to approved_peers — but we surface the reason in the
    # reply so the operator can decide to register the slice.
    if not shareable and "not in the central allowlist" not in reason:
        return f"Error: central allowlist blocks share with {peer_id}: {reason}"

    # Meeting record: must have an active meeting granting read_public.
    store = meetings.Meetings().load()
    meeting = store.get(peer_id)
    if meeting is None or not meeting.is_active() or not meeting.can("read_public"):
        return (
            f"Error: no active meeting with {peer_id} that grants read_public; "
            f"refusing to share a memex8 slice."
        )

    if not _a2a_plugin_available():
        return (
            "Error: the Hermes A2A platform plugin is not enabled; "
            "cannot send the slice."
        )

    # Build the envelope. base_url is the sender's memex8 endpoint;
    # the receiver uses it to re-fetch.
    base = memex8_client_mod._env_base_url()
    envelope = slice_mod.build_memex8_envelope(
        slice_mod.Memex8Slice(
            name=slice_name,
            memory_ids=list(memory_ids),
            base_url=base,
        ),
        to_peer=peer_id,
    )
    msg = json.dumps({"envelope": envelope, "type": "memex8_memory_slice"})

    # Approval gate — same as filesystem share_public.
    approval_request = approval.classify_task(msg)
    if approval_request is not None:
        return approval_request.to_user_block()

    reply = _call_a2a_call(agent=peer, message=msg)
    classified = _classify_peer_unreachable(peer, reply)
    if classified is not None:
        reply = classified
    return (
        f"memex8 slice '{slice_name}' sent to {peer} "
        f"(kind=memex8_memory_slice, ids={len(memory_ids)}); peer reply: {reply}"
    )


SCHEMA_RECEIVE_MEMEX8: Dict[str, Any] = {
    "name": "a2a_bridge_receive_memex8",
    "description": (
        "Process an incoming memex8_memory_slice envelope (typically "
        "from a2a_bridge_history). Verifies the signature, re-fetches "
        "each memory from the sender's memex8 base URL, re-checks "
        "visibility=public, then appends to write_to (default "
        "MEMORY.md) with a provenance comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "envelope": {
                "type": "object",
                "description": "The signed memex8 slice envelope.",
            },
            "write_to": {
                "type": "string",
                "default": "MEMORY.md",
                "description": "Filename under HERMES_HOME to write the slice into.",
            },
        },
        "required": ["envelope"],
    },
}


def handle_receive_memex8(args: Dict[str, Any], **_kw) -> str:
    envelope = args.get("envelope")
    if not isinstance(envelope, dict):
        return "Error: 'envelope' must be a JSON object."
    write_to = (args.get("write_to") or "MEMORY.md").strip()

    try:
        slice_ = slice_mod.parse_memex8_envelope(envelope)
    except slice_mod.SliceError as e:
        return f"Error: incoming memex8 slice failed verification: {e}"

    sender_id = str(envelope.get("from_peer", ""))

    # Meeting record: must have an active meeting granting read_public,
    # and the envelope's public key must match the meeting's stored
    # public key (defense in depth).
    store = meetings.Meetings().load()
    m = store.get(sender_id)
    if m is None or not m.is_active() or not m.can("read_public"):
        return (
            f"Refused: no active meeting with {sender_id} that grants read_public."
        )
    envelope_public_key = str(envelope.get("from_public_key", ""))
    if envelope_public_key and envelope_public_key != m.peer_public_key:
        return (
            f"Refused: incoming memex8 slice's from_public_key does not match "
            f"the public key on file for {sender_id}; refusing the slice."
        )

    # Re-fetch from sender's memex8. The client re-validates
    # visibility=public on every memory, so a tampered id (one that
    # wasn't in the original envelope but a malicious sender swapped
    # in) still gets caught here.
    try:
        client = memex8_client_mod.Memex8Client(base_url=slice_.base_url)
        memories = client.fetch_memex8_slice(slice_.memory_ids)
    except memex8_client_mod.Memex8ClientError as e:
        return f"Refused: could not fetch memories from sender's memex8: {e}"

    # Path-confinement for write_to, same as filesystem receive_public.
    try:
        home = Path(os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
        home_resolved = home.resolve()
        target = (home_resolved / write_to).resolve()
        if not target.is_relative_to(home_resolved):
            return (
                f"Refused: write_to={write_to!r} resolves outside HERMES_HOME; "
                f"refusing to write."
            )
    except (OSError, ValueError) as e:
        return f"Refused: bad write_to path: {e}"

    # Build the markdown body — one ## heading per memory.
    lines: List[str] = []
    lines.append(slice_mod.format_provenance(envelope))
    for mem in memories:
        mid = mem.get("id", "?")
        heading = (
            mem.get("heading")
            or mem.get("realm_name")
            or f"memex8 {mid}"
        )
        # Strip any leading '## ' from a heading the sender already
        # gave us; we add our own.
        clean_heading = heading.lstrip("# ").strip() or f"memex8 {mid}"
        body = (mem.get("content") or "").strip()
        lines.append(f"## {clean_heading}")
        lines.append("")
        if body:
            lines.append(body)
        else:
            lines.append(f"_(empty memory id={mid})_")
        lines.append("")
    block = "\n".join(lines).rstrip() + "\n"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(block)
            if not block.endswith("\n"):
                f.write("\n")
    except OSError as e:
        return f"Error: failed to write to {target}: {e}"

    return (
        f"memex8 slice '{slice_.name}' from {sender_id} accepted: "
        f"{len(memories)} memory(ies) appended to {target}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Plugin registration glue
# ─────────────────────────────────────────────────────────────────────────

A2A_BRIDGE_TOOLS: Tuple[Tuple[str, Dict[str, Any], Any], ...] = (
    ("a2a_bridge_send",                 SCHEMA_SEND,                 handle_send),
    ("a2a_bridge_confirm",              SCHEMA_CONFIRM,              handle_confirm),
    ("a2a_bridge_audit",                SCHEMA_AUDIT,                handle_audit),
    ("a2a_bridge_list_peers",           SCHEMA_LIST_PEERS,           handle_list_peers),
    ("a2a_bridge_history",              SCHEMA_HISTORY,              handle_history),
    ("a2a_bridge_shareable",            SCHEMA_SHAREABLE,            handle_shareable),
    ("a2a_bridge_introduce",            SCHEMA_INTRODUCE,            handle_introduce),
    ("a2a_bridge_introduce_respond",    SCHEMA_INTRODUCE_RESPOND,    handle_introduce_respond),
    ("a2a_bridge_meetings",             SCHEMA_MEETINGS,             handle_meetings),
    ("a2a_bridge_revoke",               SCHEMA_REVOKE,               handle_revoke),
    ("a2a_bridge_share_public",         SCHEMA_SHARE_PUBLIC,         handle_share_public),
    ("a2a_bridge_receive_public",       SCHEMA_RECEIVE_PUBLIC,       handle_receive_public),
    ("a2a_bridge_list_memex8_public",   SCHEMA_LIST_MEMEX8_PUBLIC,   handle_list_memex8_public),
    ("a2a_bridge_share_memex8",         SCHEMA_SHARE_MEMEX8,         handle_share_memex8),
    ("a2a_bridge_receive_memex8",       SCHEMA_RECEIVE_MEMEX8,       handle_receive_memex8),
)


def A2A_BRIDGE_CHECK() -> bool:
    """The plugin's tools register if the A2A platform plugin is *loadable* —
    not just available. The actual error message at call time tells the user
    what to do (enable the platform plugin). This keeps the wrapper
    visible in the toolset so the user sees the audit/history tools even
    if the platform plugin hasn't been enabled yet.
    """
    return True