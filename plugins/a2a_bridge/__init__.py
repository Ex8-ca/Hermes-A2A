"""a2a-bridge plugin — one-line inter-agent tasks with audit + approval.

Wraps the Hermes A2A platform plugin's outbound client tools
(a2a_call / a2a_list / a2a_history / a2a_discover) with:

  * Reply formatting that surfaces task state + context_id + elapsed time
    in a single block, so the model doesn't have to parse raw JSON-RPC.
  * Audit-log lookup against ~/.hermes/a2a_audit.jsonl — recent
    exchanges with a given peer, both directions, in a small table.
  * An approval gate that intercepts tasks whose message looks like a
    memory share, persona edit, or credential exchange, and returns a
    structured approval payload instead of firing the call.
  * (v0.1.x+) A dry-run helper that reports which memories and skills
    are shareable under the central allowlist + frontmatter policy.
  * (v0.2) Public-share tools, the meeting protocol, and ed25519
    identity — see ROADMAP.md.

The plugin does NOT open its own network sockets — it goes through
the underlying a2a plugin, which already handles auth, redaction, rate limit,
audit logging, and conversation persistence. This plugin is a UX layer.

Loaded by the agent when the plugin is enabled (`hermes plugins enable a2a_bridge`).
"""

from __future__ import annotations

import logging

from plugins.a2a_bridge import identity, policy  # noqa: F401  (re-exported for callers)
from plugins.a2a_bridge.tools import (
    A2A_BRIDGE_CHECK,
    A2A_BRIDGE_TOOLS,
    SCHEMA_AUDIT,
    SCHEMA_CONFIRM,
    SCHEMA_HISTORY,
    SCHEMA_LIST_PEERS,
    SCHEMA_SEND,
    SCHEMA_SHAREABLE,
    handle_audit,
    handle_confirm,
    handle_history,
    handle_list_peers,
    handle_send,
    handle_shareable,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the wrapper tools with the agent runtime."""
    # Stash the plugin context so handlers can dispatch_tool() back into
    # the runtime (e.g. to invoke a2a_call without re-importing it).
    from plugins.a2a_bridge import tools as _tools_mod
    _tools_mod._PLUGIN_CTX = ctx

    for name, schema, handler in A2A_BRIDGE_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="a2a_bridge",
            schema=schema,
            handler=handler,
            check_fn=A2A_BRIDGE_CHECK,
            emoji="🔗",
        )
    logger.info(
        "a2a-bridge plugin: registered %d tools (send/confirm/audit/list/history/shareable)",
        len(A2A_BRIDGE_TOOLS),
    )


# Keep references alive so linters don't flag the imports as unused.
_ = (
    SCHEMA_SEND,
    SCHEMA_CONFIRM,
    SCHEMA_AUDIT,
    SCHEMA_LIST_PEERS,
    SCHEMA_HISTORY,
    SCHEMA_SHAREABLE,
    handle_send,
    handle_confirm,
    handle_audit,
    handle_list_peers,
    handle_history,
    handle_shareable,
)
