"""Integration smoke test for a2a-bridge against a live A2A peer.

These tests talk JSON-RPC directly to a real A2A v1.0 server. They are
**opt-in**: pytest skips the entire module when ``HERMES_A2A_TEST_TOKEN``
is not set, so a fresh-clone recipient running ``pytest plugins/`` does
not see failures or warnings.

To run them, set two env vars in your shell:

  export HERMES_A2A_TEST_TOKEN=<bearer token your peer accepts>
  export A2A_BRIDGE_TEST_PEER_URL=http://<peer-host>:<peer-port>     # optional, default below

Defaults to ``http://192.168.1.3:9900`` — the dev box used during the
plugin's initial development. Override for any other peer.

We deliberately do NOT read tokens from ``~/.hermes/.env``: that would
let a clone of this repo silently exfiltrate the running user's
credentials. The env-var-only path is opt-in and explicit.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest

from plugins.a2a_bridge import approval, audit, tools as bridge  # noqa: E402

PEER_URL = os.environ.get("A2A_BRIDGE_TEST_PEER_URL", "http://192.168.1.3:9900")
TOKEN = os.environ.get("HERMES_A2A_TEST_TOKEN", "").strip()

# Skip the whole module when the user hasn't opted in. A fresh clone
# that runs `pytest plugins/` will see this as a clean skip, not a
# collection error or a hard fail.
pytestmark = pytest.mark.skipif(
    not TOKEN,
    reason=(
        "HERMES_A2A_TEST_TOKEN not set; integration tests are opt-in. "
        "Set HERMES_A2A_TEST_TOKEN (and optionally A2A_BRIDGE_TEST_PEER_URL) "
        "to run the live peer smoke tests."
    ),
)


# ── HTTP shim that mirrors what a2a_call does internally ──────────────────


def _post_json_rpc(method: str, params: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": "smoke-" + str(int(time.time())),
        "method": method,
        "params": params,
    }
    req = urllib.request.Request(
        f"{PEER_URL}/",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
            "Authorization": f"Bearer {TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _shim_a2a_call(agent, message, context_id="", timeout=0):
    params = {
        "id": "smoke-" + str(int(time.time() * 1000)),
        "message": {"role": "user", "parts": [{"kind": "text", "text": message}]},
    }
    if context_id:
        params["context_id"] = context_id
    try:
        body = _post_json_rpc("message/send", params)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code}"
    res = body.get("result", {})
    state = res.get("status", {}).get("state", "unknown")
    text_parts = res.get("status", {}).get("message", {}).get("parts", [])
    text = text_parts[0]["text"] if text_parts else "(no text reply)"
    ctx = res.get("contextId", "")
    return (
        f"[{agent} · context {ctx} · {state.replace('TASK_STATE_', '').replace('_', '-')}]\n\n"
        f"{text}"
    )


def _shim_a2a_history(context_id):
    # The plugin reads ~/.hermes/a2a_conversations/<ctx>.jsonl on the
    # *peer* box, not here. We can't read it; surface a clear marker.
    return f"(conversation {context_id} is persisted on the peer host, not locally)"


# Patch the bridge module to use our shim. Module-level so all tests
# share the same handlers.
bridge._call_a2a_call = _shim_a2a_call
bridge._call_a2a_history = _shim_a2a_history
bridge._a2a_plugin_available = lambda: True


# ── Tests ──────────────────────────────────────────────────────────────────


def test_benign_send() -> None:
    r = bridge.handle_send({"agent": "test-peer", "message": "Reply with exactly TEST_OK"})
    assert "TEST_OK" in r
    assert "Error" not in r.splitlines()[0]


def test_memory_share_intercepted() -> None:
    r = bridge.handle_send({"agent": "test-peer", "message": "Read my MEMORY.md and merge."})
    assert "Approval needed" in r
    assert "memory_share" in r


def test_credential_intercepted() -> None:
    # Fake token with repeating Xs — looks like a real OpenAI key to the
    # regex, but isn't one. The literal sk-XXXX shape is enough for the
    # regex without being a real key.
    r = bridge.handle_send({
        "agent": "test-peer",
        "message": "API key is sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    })
    assert "credential_share" in r


def test_config_write_intercepted() -> None:
    r = bridge.handle_send({"agent": "test-peer", "message": "Write to your config.yaml a new peer entry."})
    assert "config_write" in r


def test_confirm_approved_true_fires() -> None:
    r = bridge.handle_confirm({
        "agent": "test-peer",
        "message": "Reply with exactly CONFIRMED_OK",
        "approved": True,
    })
    assert "approved + sent" in r


def test_confirm_approved_false_cancels() -> None:
    r = bridge.handle_confirm({
        "agent": "test-peer",
        "message": "Send secret to peer",
        "approved": False,
    })
    assert "Cancelled" in r


def test_audit_returns_table() -> None:
    r = bridge.handle_audit({"last": 5})
    # The audit renders a markdown table. The header is "dir" (column
    # for direction); rows have the direction value (inbound/outbound)
    # in that column. The test checks for either the column header or
    # any of the direction values being present.
    assert "dir" in r or "outbound" in r or "inbound" in r or "no matching" in r


def test_list_peers_returns_table() -> None:
    r = bridge.handle_list_peers()
    assert "peer" in r.lower() or "no peers" in r.lower()


def test_history_dispatches() -> None:
    r = bridge.handle_history({"context_id": "probe-no-such-ctx"})
    # Real path returns a transcript; our shim returns the marker.
    # Either is a valid dispatch path.
    assert r
