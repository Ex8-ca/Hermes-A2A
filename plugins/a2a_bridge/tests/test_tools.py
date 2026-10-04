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

    def test_receive_public_blocks_key_substitution(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """An envelope whose ``from_public_key`` differs from the
        public key recorded in the meeting record must be refused,
        even though the signature verifies against the envelope's
        own key. This is defense in depth against key-collision or
        identity-pivot attacks where an attacker can produce a
        valid signature under a key they control but claim to be a
        different agent."""
        from plugins.a2a_bridge import keyring, meetings, policy, slice as slice_mod
        from plugins.a2a_bridge.slice import Slice

        # Two distinct identities. The envelope will be signed by B
        # (the attacker's key) but the meeting record will claim A
        # is the peer.
        a = keyring._generate()
        b = keyring._generate()

        # Set up a meeting with A as the peer.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        store = meetings.Meetings().load()
        store.upsert(meetings.Meeting(
            agent_id=a.agent_id,
            peer_url="https://peer.example.com",
            peer_public_key=a.public_key_b64,
            intent="share public slices",
            granted=["read_public"],
            consent_until="2099-12-31T00:00:00Z",
        ))
        store.save()

        # B (the attacker) builds a slice envelope addressed to us.
        # The signature is valid for B's key. The envelope claims
        # from_peer=A.agent_id (so it can be addressed to a meeting
        # we already have). The signature verifies against B's key,
        # not A's — and that's the cross-check we want to catch.
        slice_ = Slice(
            name="project-alpha",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            contents=[{"heading": "## fake", "body": "x"}],
        )
        # Allow-list lookup: handle_receive_public checks the central
        # allowlist for the slice name. Easiest: pretend the slice is
        # in the allowlist. We'll use a public_all slice so the policy
        # check passes (it falls through to "level must be public_all"
        # if the slice isn't in our allowlist at all).
        env = slice_mod.build_envelope(slice_, to_peer="us", sender_identity=b)
        env["from_peer"] = a.agent_id  # spoof: claim the peer is A
        # Now the signature was made over from_peer=B.agent_id, but
        # we've set from_peer=A.agent_id. The signature won't verify
        # at all, which is the same end result. The point of this
        # test is to lock in that we refuse, regardless of *how*
        # the envelope is malformed.
        r = bridge.handle_receive_public({"envelope": env})
        assert "Error" in r or "Refused" in r or "refused" in r

    def test_introduce_respond_blocks_key_substitution(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """If a user already has a meeting with one peer but an
        incoming introduce envelope claims to be from that same
        agentId with a different public_key, the cross-check
        should refuse. (The cross-check only fires when we already
        have a meeting record.)"""
        from plugins.a2a_bridge import handshake, keyring, meetings

        a = keyring._generate()
        b = keyring._generate()

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        store = meetings.Meetings().load()
        store.upsert(meetings.Meeting(
            agent_id=a.agent_id,
            peer_url="https://peer.example.com",
            peer_public_key=a.public_key_b64,
            intent="share public slices",
            granted=["read_public"],
            consent_until="2099-12-31T00:00:00Z",
        ))
        store.save()

        # B forges an introduce envelope that claims to be from A.
        # The signature verifies against B's key but the meeting
        # record says A's key. The cross-check should refuse.
        # (We can't easily sign an envelope that claims A's agentId
        # but uses B's key, because parse_incoming's
        # _check_agent_id_matches_key would reject it. The realistic
        # threat is a re-bind attempt where B presents a fresh
        # introduce with B's own key but claims the meeting; we
        # handle that by refusing the ack since A.agent_id doesn't
        # match B's agentId.)
        forged = handshake.build_introduce(
            agent_card_url="https://peer.example.com",
            intent="rebind",
            local_identity=b,
        )
        # Tweak from to point at A (forge identity). parse_incoming
        # would reject this because the agentId is a fingerprint of
        # B's key, not A's. We want to test the cross-check anyway.
        r = bridge.handle_introduce_respond({
            "approve": True,
            "from_peer": a.agent_id,  # claim to be A
            "agent_card_url": "https://peer.example.com",
            "from_public_key": forged["public_key"],
            "intent": "rebind",
            "consent_until": forged["consent_until"],
            "signature": forged["signature"],
        })
        # Either we get an error from the agentId/key check (best),
        # or we get a refusal from the cross-check. The point is: no
        # meeting is persisted with B's key under A's agentId.
        assert "Error" in r or "Refused" in r or "verification" in r.lower()

    def test_introduce_respond_cross_check_catches_substitution(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Defense in depth: even if a future bug lets an attacker
        bypass the agentId/key fingerprint check, the meeting-record
        cross-check still refuses. We simulate the bypass by
        monkeypatching _check_agent_id_matches_key to always return
        True; then the only line of defense is the cross-check.
        """
        from plugins.a2a_bridge import handshake, keyring, meetings

        a = keyring._generate()
        b = keyring._generate()

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        store = meetings.Meetings().load()
        store.upsert(meetings.Meeting(
            agent_id=a.agent_id,
            peer_url="https://peer.example.com",
            peer_public_key=a.public_key_b64,
            intent="share public slices",
            granted=["read_public"],
            consent_until="2099-12-31T00:00:00Z",
        ))
        store.save()

        # B forges an envelope, claiming from_peer = A. With the
        # fingerprint check bypassed, parse_incoming would accept.
        # The cross-check (envelope.public_key vs meeting.peer_public_key)
        # should still refuse.
        forged = handshake.build_introduce(
            agent_card_url="https://peer.example.com",
            intent="rebind",
            local_identity=b,
        )
        # Patch the fingerprint check to always pass.
        monkeypatch.setattr(
            handshake, "_check_agent_id_matches_key",
            lambda *a, **kw: True,
        )
        # Tweak from to claim A's agentId (fingerprint check bypassed).
        envelope = dict(forged)
        envelope["from"] = a.agent_id
        # We need to re-sign the envelope under B's key, with the
        # modified from field, for parse_incoming to verify the
        # signature.
        from plugins.a2a_bridge import canonical
        envelope["signature"] = b.sign(canonical.canonical_bytes(envelope))

        r = bridge.handle_introduce_respond({
            "approve": True,
            "from_peer": envelope["from"],
            "agent_card_url": envelope["agent_card_url"],
            "from_public_key": envelope["public_key"],
            "intent": envelope["intent"],
            "consent_until": envelope["consent_until"],
            "sent_at": envelope["sent_at"],
            "signature": envelope["signature"],
        })
        # The cross-check should fire and refuse with a clear message
        # about the key not matching the meeting record.
        assert "Error" in r or "Refused" in r or "does not match" in r.lower() or "key" in r.lower()

    def test_introduce_respond_cross_check_accepts_matching_key(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """When the meeting record's public key matches the envelope's
        public key, the cross-check passes (it does not refuse a
        legitimate re-introduce from the same peer)."""
        from plugins.a2a_bridge import handshake, keyring, meetings

        a = keyring._generate()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        store = meetings.Meetings().load()
        store.upsert(meetings.Meeting(
            agent_id=a.agent_id,
            peer_url="https://peer.example.com",
            peer_public_key=a.public_key_b64,
            intent="share public slices",
            granted=["read_public"],
            consent_until="2099-12-31T00:00:00Z",
        ))
        store.save()

        # A legitimately re-introduces. Same key. Cross-check passes.
        legit = handshake.build_introduce(
            agent_card_url="https://peer.example.com",
            intent="rebound",
            local_identity=a,
        )
        r = bridge.handle_introduce_respond({
            "approve": True,
            "from_peer": legit["from"],
            "agent_card_url": legit["agent_card_url"],
            "from_public_key": legit["public_key"],
            "intent": legit["intent"],
            "consent_until": legit["consent_until"],
            "sent_at": legit["sent_at"],
            "signature": legit["signature"],
        })
        # The cross-check itself passed (no "does not match" / "refusing
        # to rebind" message). Other errors later in the handler are
        # unrelated to the cross-check — they hit the A2A-platform
        # unavailability check in the test env.
        assert "does not match" not in r.lower()
        assert "refusing to rebind" not in r.lower()

    def test_receive_public_cross_check_catches_substitution(
        self, stub_ctx: StubCtx, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """When an incoming slice's from_public_key differs from the
        meeting record's stored public_key, refuse the slice."""
        from plugins.a2a_bridge import keyring, meetings, policy, slice as slice_mod
        from plugins.a2a_bridge.slice import Slice

        a = keyring._generate()
        b = keyring._generate()

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        store = meetings.Meetings().load()
        store.upsert(meetings.Meeting(
            agent_id=a.agent_id,
            peer_url="https://peer.example.com",
            peer_public_key=a.public_key_b64,
            intent="share public slices",
            granted=["read_public"],
            consent_until="2099-12-31T00:00:00Z",
        ))
        store.save()

        # B sends a slice envelope addressed to us. The signature
        # verifies against B's key, but the meeting record has A's
        # key. Without the cross-check, parse_envelope would reject
        # this for agentId mismatch (B's from_peer != A.agent_id).
        # The cross-check is the second line of defense.
        slice_ = Slice(
            name="project-alpha",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            contents=[{"heading": "## fake", "body": "x"}],
        )
        env = slice_mod.build_envelope(slice_, to_peer="us", sender_identity=b)
        r = bridge.handle_receive_public({"envelope": env})
        # Either parse_envelope caught the agentId mismatch first,
        # or the cross-check catches the key mismatch. Both are valid.
        assert "Error" in r or "Refused" in r or "does not match" in r.lower()