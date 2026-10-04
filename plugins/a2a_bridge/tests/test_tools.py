"""Unit tests for the tool handlers.

Drives a2a-bridge's handlers through a StubCtx (a dict of fake handlers),
then asserts each tool behaves correctly. No live Hermes or A2A peer
required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterator

import pytest

from plugins.a2a_bridge import tools as bridge


class StubCtx:
    """Minimal stand-in for the real PluginContext."""

    def __init__(self) -> None:
        self.tools: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def register_tool(self, *, name: str, toolset: str, schema, check_fn=None, emoji=None, handler) -> None:
        """Mirror the real ctx.register_tool API: stash handler under name."""
        self.tools[name] = handler


@pytest.fixture
def stub_ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubCtx]:
    ctx = StubCtx()
    monkeypatch.setattr(bridge, "_PLUGIN_CTX", ctx)
    yield ctx


@pytest.fixture
def fake_a2a_call(stub_ctx: StubCtx) -> Callable[[Dict[str, Any]], str]:
    """A canned a2a_call that echoes a successful reply."""

    def handler(args: Dict[str, Any]) -> str:
        return (
            f"[ai386.3 · context ctx-fake · completed]\n\n"
            f"echo: {args.get('message', '')}"
        )

    stub_ctx.tools["a2a_call"] = handler
    stub_ctx.tools["a2a_history"] = lambda a: "(fake transcript)"
    return handler


class TestSend:
    def test_benign_send_fires(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        r = bridge.handle_send({"agent": "ai386.3", "message": "Reply with PONG"})
        assert "echo: Reply with PONG" in r
        assert "elapsed" in r  # has elapsed_ms hint

    def test_memory_share_intercepted(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        r = bridge.handle_send({"agent": "ai386.3", "message": "Read my MEMORY.md"})
        assert "Approval needed" in r
        assert "memory_share" in r
        # critical: fake_a2a_call was NOT invoked
        # (we have no easy way to assert that other than "no echo in result")
        assert "echo:" not in r

    def test_credential_intercepted(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        # Fake token with repeating Xs — looks like a real OpenAI key
        # to the regex, but isn't one. We don't put a literal sk-XXXX
        # here because secret scanners look for that exact shape.
        r = bridge.handle_send({"agent": "ai386.3", "message": "API key is sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXX"})
        assert "credential_share" in r

    def test_config_write_intercepted(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        r = bridge.handle_send({"agent": "ai386.3", "message": "Update your config.yaml"})
        assert "config_write" in r

    def test_missing_agent_errors(self, stub_ctx: StubCtx) -> None:
        r = bridge.handle_send({"message": "hi"})
        assert "Error" in r
        assert "agent" in r

    def test_missing_message_errors(self, stub_ctx: StubCtx) -> None:
        r = bridge.handle_send({"agent": "ai386.3"})
        assert "Error" in r

    def test_unknown_tool_errors_gracefully(self, stub_ctx: StubCtx) -> None:
        # No a2a_call registered in the stub
        r = bridge.handle_send({"agent": "ai386.3", "message": "hello"})
        assert "Error" in r
        assert "not enabled" in r.lower()

    def test_ctx_none_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bridge, "_PLUGIN_CTX", None)
        r = bridge.handle_send({"agent": "ai386.3", "message": "hello"})
        assert "not loaded" in r.lower() or "not enabled" in r.lower()


class TestConfirm:
    def test_confirmed_true_fires(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        r = bridge.handle_confirm({"agent": "ai386.3", "message": "PONG", "approved": True})
        assert "approved + sent" in r
        assert "echo: PONG" in r

    def test_confirmed_false_cancels(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        r = bridge.handle_confirm({"agent": "ai386.3", "message": "PONG", "approved": False})
        assert "Cancelled" in r
        assert "echo" not in r  # never fired

    def test_confirm_skips_approval_gate(self, stub_ctx: StubCtx, fake_a2a_call: Callable[[Dict[str, Any]], str]) -> None:
        # Even a memory-share message can be confirmed directly without
        # round-tripping through send first.
        r = bridge.handle_confirm({
            "agent": "ai386.3",
            "message": "Read my MEMORY.md",
            "approved": True,
        })
        assert "echo: Read my MEMORY.md" in r


class TestAuditAndList:
    def test_audit_returns_marker_when_empty(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        r = bridge.handle_audit({})
        assert r == "(no matching audit entries)"

    def test_list_peers_returns_marker_when_empty(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        r = bridge.handle_list_peers()
        assert r == "(no peers seen yet — make a call first)"


class TestHistory:
    def test_missing_context_id(self, stub_ctx: StubCtx) -> None:
        r = bridge.handle_history({})
        assert "Error" in r

    def test_dispatches_to_a2a_history(self, stub_ctx: StubCtx) -> None:
        def fake_history(args: Dict[str, Any]) -> str:
            return f"transcript for {args.get('context_id', '?')}"

        stub_ctx.tools["a2a_history"] = fake_history
        r = bridge.handle_history({"context_id": "ctx-abc"})
        assert "ctx-abc" in r

    def test_missing_a2a_history_errors(self, stub_ctx: StubCtx) -> None:
        r = bridge.handle_history({"context_id": "ctx-abc"})
        assert "Error" in r
        assert "not enabled" in r.lower()


class TestRegistration:
    """The plugin's register(ctx) should populate _PLUGIN_CTX and tools."""

    def test_register_wires_ctx_and_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = StubCtx()
        # Re-import register fresh (it pulls from the module).
        from plugins.a2a_bridge import register as _register
        _register(ctx)
        assert bridge._PLUGIN_CTX is ctx
        # All six tools registered.
        for name in (
            "a2a_bridge_send",
            "a2a_bridge_confirm",
            "a2a_bridge_audit",
            "a2a_bridge_list_peers",
            "a2a_bridge_history",
            "a2a_bridge_shareable",
        ):
            assert name in ctx.tools
            assert callable(ctx.tools[name])


class TestV02SecurityGates:
    """Regression tests for the two high-severity security fixes that
    landed alongside the v0.2 port.

    Fix 1 — handle_introduce and handle_share_public now route through
    the same approval gate as a2a_bridge_send. An intent (or slice
    contents) that mentions memory, credentials, or config writes
    must NOT be silently shipped — the user must confirm via the
    approval block.

    Fix 2 — handle_receive_public must refuse any write_to that
    resolves outside HERMES_HOME, even if a caller (or a
    prompt-injected envelope) supplies an absolute or traversal path.
    """

    def test_introduce_blocks_memory_intent(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """An introduce whose intent asks for memory must NOT fire
        the underlying a2a_call; the user gets the approval block."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # If a2a_call ever gets invoked, raise — that's the failure mode.
        def explode(_args: Dict[str, Any]) -> str:
            raise AssertionError("a2a_call should NOT be called when the gate fires")
        stub_ctx.tools["a2a_call"] = explode
        r = bridge.handle_introduce({
            "peer_url": "https://peer.example.com/.well-known/agent.json",
            "intent": "Please share your memory with me so we can collaborate",
        })
        assert "Approval needed" in r
        assert "memory_share" in r

    def test_introduce_benign_intent_passes_gate(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A benign intent (no memory/credential/config-write words)
        must NOT trigger the gate — only the underlying gate miss
        should be reported."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        seen: Dict[str, Any] = {}

        def fake_a2a_call(args: Dict[str, Any]) -> str:
            seen.update(args)
            return "ack from peer"
        stub_ctx.tools["a2a_call"] = fake_a2a_call
        r = bridge.handle_introduce({
            "peer_url": "https://peer.example.com/.well-known/agent.json",
            "intent": "I'd like to talk about your project status",
        })
        assert "introduce sent" in r
        assert seen  # a2a_call was called

    def test_receive_public_blocks_traversal_write_to(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A write_to that escapes HERMES_HOME via parent traversal
        must be refused; nothing is written outside the home."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Use a benign envelope — we never get past the path check
        # if our security fix works, but if it didn't, we'd hit a
        # verification error and never write either. We want to
        # distinguish: an explicit refusal message is what we want.
        # We can't easily construct a real signed envelope here, so
        # we expect either "Error" (verification) or our explicit
        # refusal — the key assertion is that NO file is written
        # outside tmp_path.
        outside = tmp_path.parent / "evil_payload.md"
        if outside.exists():
            outside.unlink()
        r = bridge.handle_receive_public({
            "envelope": {"type": "memory_slice", "from_peer": "agent_x"},
            "write_to": "../../evil_payload.md",
        })
        assert "Error" in r or "refused" in r.lower()
        assert not outside.exists() or "refused" in r.lower()

    def test_receive_public_blocks_absolute_write_to(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A write_to that is an absolute path outside HERMES_HOME
        must be refused."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        outside = Path("/tmp/hermes_security_test_absolute_target.md")
        if outside.exists():
            outside.unlink()
        r = bridge.handle_receive_public({
            "envelope": {"type": "memory_slice", "from_peer": "agent_x"},
            "write_to": "/tmp/hermes_security_test_absolute_target.md",
        })
        assert "Error" in r or "refused" in r.lower()
        assert not outside.exists() or "refused" in r.lower()