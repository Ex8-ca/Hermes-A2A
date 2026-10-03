"""Tool implementations for a2a-bridge.

Five core tools (v0.1.x):

  a2a_bridge_send          — initiate a task. May return an approval request.
  a2a_bridge_confirm       — confirm (or cancel) a pending approval, then send.
  a2a_bridge_audit         — recent exchanges for a peer from the audit log.
  a2a_bridge_list_peers    — peers seen in the audit log (in addition to config).
  a2a_bridge_history       — recall a prior A2A conversation by context_id.

Plus one v0.1.x dry-run helper:

  a2a_bridge_shareable     — list every slice in the central allowlist
                             and report whether each is shareable with a
                             given peer (according to the frontmatter
                             + policy-layer resolver). Read-only; no
                             actual sharing. Full public-share tools
                             (share_public, request_public) ship in v0.2
                             (see ROADMAP.md).

`a2a_bridge_send` and `a2a_bridge_confirm` delegate to the underlying
Hermes A2A platform plugin's `a2a_call`. The platform plugin does the
real network call, auth, audit, persistence, redaction, and rate limit.

When the underlying a2a plugin is not enabled or has no peers, the tools
surface a structured error instead of crashing.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Tuple

from plugins.a2a_bridge import approval, audit, identity, policy  # noqa: F401

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
        # If the user passed an agent_id, we don't have a URL to normalize;
        # we use the agent_id as-is for the policy resolver.
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
# Plugin registration glue
# ─────────────────────────────────────────────────────────────────────────

A2A_BRIDGE_TOOLS: Tuple[Tuple[str, Dict[str, Any], Any], ...] = (
    ("a2a_bridge_send",       SCHEMA_SEND,       handle_send),
    ("a2a_bridge_confirm",    SCHEMA_CONFIRM,    handle_confirm),
    ("a2a_bridge_audit",      SCHEMA_AUDIT,      handle_audit),
    ("a2a_bridge_list_peers", SCHEMA_LIST_PEERS, handle_list_peers),
    ("a2a_bridge_history",    SCHEMA_HISTORY,    handle_history),
    ("a2a_bridge_shareable",  SCHEMA_SHAREABLE,  handle_shareable),
)


def A2A_BRIDGE_CHECK() -> bool:
    """The plugin's tools register if the A2A platform plugin is *loadable* —
    not just available. The actual error message at call time tells the user
    what to do (enable the platform plugin). This keeps the wrapper
    visible in the toolset so the user sees the audit/history tools even
    if the platform plugin hasn't been enabled yet.
    """
    return True