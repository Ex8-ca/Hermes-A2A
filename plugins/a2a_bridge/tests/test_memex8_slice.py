"""Tests for the memex8-backed slice transport.

Three layers:
  1. ``Memex8Slice`` envelope build/parse round-trip (no network).
  2. ``Memex8Client`` HTTP behavior (mocked with ``requests-mock``-style
     monkeypatching; we don't add a new dep, just patch
     ``memex8_client.requests.request``).
  3. End-to-end ``handle_share_memex8`` / ``handle_receive_memex8``
     flow with the policy + meeting layers mocked.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from plugins.a2a_bridge import (
    identity,
    keyring,
    meetings,
    policy,
    slice as slice_mod,
)
from plugins.a2a_bridge import memex8_client as memex8_client_mod
from plugins.a2a_bridge import tools as tools_mod


# ───────────────────────────────────────────────────────────────────────
# Envelope build / parse
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def memex8_slice() -> slice_mod.Memex8Slice:
    return slice_mod.Memex8Slice(
        name="memex8-shareable",
        memory_ids=["abc-123", "def-456"],
        base_url="http://192.168.1.3:8080",
    )


def test_build_memex8_envelope_shape(memex8_slice: slice_mod.Memex8Slice) -> None:
    ident = keyring._generate()
    env = slice_mod.build_memex8_envelope(
        memex8_slice, to_peer="agent_zzzzzzzzzzzz", sender_identity=ident
    )
    assert env["kind"] == "memex8_memory_slice"
    assert env["from_peer"] == ident.agent_id
    assert env["from_public_key"] == ident.public_key_b64
    assert env["to_peer"] == "agent_zzzzzzzzzzzz"
    assert env["slice_name"] == "memex8-shareable"
    assert env["memex8_base_url"] == "http://192.168.1.3:8080"
    assert env["memory_ids"] == ["abc-123", "def-456"]
    assert "signature" in env
    assert "sent_at" in env


def test_parse_memex8_envelope_round_trip(memex8_slice: slice_mod.Memex8Slice) -> None:
    ident = keyring._generate()
    env = slice_mod.build_memex8_envelope(
        memex8_slice, to_peer=ident.agent_id, sender_identity=ident
    )
    parsed = slice_mod.parse_memex8_envelope(env)
    assert parsed.name == memex8_slice.name
    assert parsed.memory_ids == memex8_slice.memory_ids
    assert parsed.base_url == memex8_slice.base_url


def test_parse_memex8_envelope_rejects_tampered_id(
    memex8_slice: slice_mod.Memex8Slice,
) -> None:
    ident = keyring._generate()
    env = slice_mod.build_memex8_envelope(
        memex8_slice, to_peer="agent_zzzzzzzzzzzz", sender_identity=ident
    )
    # Tamper: swap one of the memory ids. The signature covers the
    # original list, so parse must fail.
    env["memory_ids"] = ["abc-123", "ATTACKER-PRIVATE-ID"]
    with pytest.raises(slice_mod.SliceError, match="signature"):
        slice_mod.parse_memex8_envelope(env)


def test_parse_memex8_envelope_rejects_non_http_url(
    memex8_slice: slice_mod.Memex8Slice,
) -> None:
    ident = keyring._generate()
    env = slice_mod.build_memex8_envelope(
        memex8_slice, to_peer="agent_zzzzzzzzzzzz", sender_identity=ident
    )
    env["memex8_base_url"] = "file:///etc/passwd"
    env["signature"] = ident.sign(slice_mod.canonical.canonical_bytes(env))
    with pytest.raises(slice_mod.SliceError, match="http"):
        slice_mod.parse_memex8_envelope(env)


def test_parse_memex8_envelope_rejects_empty_memory_ids(
    memex8_slice: slice_mod.Memex8Slice,
) -> None:
    ident = keyring._generate()
    env = slice_mod.build_memex8_envelope(
        memex8_slice, to_peer="agent_zzzzzzzzzzzz", sender_identity=ident
    )
    env["memory_ids"] = []
    env["signature"] = ident.sign(slice_mod.canonical.canonical_bytes(env))
    with pytest.raises(slice_mod.SliceError, match="memory_ids"):
        slice_mod.parse_memex8_envelope(env)


# ───────────────────────────────────────────────────────────────────────
# Memex8Client HTTP behavior
# ───────────────────────────────────────────────────────────────────────


def test_memex8_client_list_public_parses_response() -> None:
    fake = {
        "memories": [
            {"id": "x", "heading": "Hi", "realm_name": "personal",
             "content_preview": "hello world", "tags": []}
        ],
        "total": 1, "limit": 20, "offset": 0,
    }
    with mock.patch.object(
        memex8_client_mod.requests, "request", return_value=_FakeResp(200, fake)
    ) as req:
        c = memex8_client_mod.Memex8Client(base_url="http://x", api_key="k")
        out = c.list_public(limit=20)
    assert out["total"] == 1
    # Make sure we sent the bearer token.
    sent_headers = req.call_args.kwargs["headers"]
    assert sent_headers.get("Authorization") == "Bearer k"
    assert sent_headers.get("Content-Type") == "application/json"
    # Make sure we hit the discovery path.
    assert "/api/v1/memories/public" in req.call_args.args[1]


def test_memex8_client_get_memory_404_raises() -> None:
    with mock.patch.object(
        memex8_client_mod.requests, "request",
        return_value=_FakeResp(404, {"error": "not found"}),
    ):
        c = memex8_client_mod.Memex8Client(base_url="http://x", api_key="k")
        with pytest.raises(memex8_client_mod.Memex8ClientError):
            c.get_memory("nope")


def test_memex8_client_fetch_memex8_slice_refuses_private() -> None:
    # First memory public, second private — the whole batch must fail.
    public_body = {"id": "a", "visibility": "public", "content": "ok"}
    private_body = {"id": "b", "visibility": "private", "content": "secret"}
    def fake_request(method, url, **kwargs):
        if url.endswith("/a"):
            return _FakeResp(200, public_body)
        if url.endswith("/b"):
            return _FakeResp(200, private_body)
        return _FakeResp(404, {"error": "x"})
    with mock.patch.object(
        memex8_client_mod.requests, "request", side_effect=fake_request
    ):
        c = memex8_client_mod.Memex8Client(base_url="http://x", api_key="k")
        with pytest.raises(memex8_client_mod.Memex8ClientError, match="not visibility=public"):
            c.fetch_memex8_slice(["a", "b"])


def test_memex8_client_fetch_memex8_slice_legit() -> None:
    bodies = [
        {"id": "a", "visibility": "public", "content": "ok"},
        {"id": "b", "visibility": "public", "content": "also ok"},
    ]
    seq = iter(bodies)
    def fake_request(method, url, **kwargs):
        return _FakeResp(200, next(seq))
    with mock.patch.object(
        memex8_client_mod.requests, "request", side_effect=fake_request
    ):
        c = memex8_client_mod.Memex8Client(base_url="http://x", api_key="k")
        out = c.fetch_memex8_slice(["a", "b"])
    assert [m["id"] for m in out] == ["a", "b"]


class _FakeResp:
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


# ───────────────────────────────────────────────────────────────────────
# Sender / receiver end-to-end
# ───────────────────────────────────────────────────────────────────────


def _setup_active_meeting(peer_id: str, peer_url: str) -> None:
    """Install a meeting record in the operator's meetings file."""
    from plugins.a2a_bridge import meetings
    store = meetings.Meetings()
    store.load()
    store.upsert(meetings.Meeting(
        agent_id=peer_id,
        peer_url=peer_url,
        peer_public_key=keyring._generate().public_key_b64,  # any valid key
        granted=["read_public"],
        established_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        consent_until=(datetime.now(timezone.utc) + timedelta(days=1))
            .isoformat().replace("+00:00", "Z"),
    ))
    store.save()


def test_share_memex8_refuses_private_memory(tmp_path: Path) -> None:
    # The preflight calls get_memory() for each id; one returns private.
    def fake_get_memory(mid: str) -> Dict[str, Any]:
        if mid == "good":
            return {"id": "good", "visibility": "public"}
        return {"id": mid, "visibility": "private"}
    with mock.patch.object(memex8_client_mod, "Memex8Client") as mc:
        mc.return_value.get_memory.side_effect = fake_get_memory
        out = tools_mod.handle_share_memex8({
            "peer": "http://peer.test:9900",
            "slice_name": "share",
            "memory_ids": ["good", "bad"],
        })
    assert "visibility=" in out and "refusing" in out


def test_share_memex8_requires_active_meeting(tmp_path: Path, monkeypatch) -> None:
    # No meeting on file. Should refuse with a clear message.
    # Point meetings file at a temp path so we don't pollute real config.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    def fake_get_memory(mid: str) -> Dict[str, Any]:
        return {"id": mid, "visibility": "public"}
    with mock.patch.object(memex8_client_mod, "Memex8Client") as MC:
        MC.return_value.get_memory.side_effect = fake_get_memory
        with mock.patch.object(tools_mod, "_resolve_peer_entry", return_value={
            "url": "http://peer.test:9900",
        }):
            out = tools_mod.handle_share_memex8({
                "peer": "http://peer.test:9900",
                "slice_name": "share",
                "memory_ids": ["x"],
            })
    assert "no active meeting" in out


def test_receive_memex8_refuses_private_after_refetch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # The receiver stores meetings keyed by the *sender's* agent_id
    # (the from_peer of any envelope we accept from them).
    sender_ident = keyring._generate()
    sender_id = sender_ident.agent_id
    # Build a legit envelope addressed to a hypothetical receiver.
    env = slice_mod.build_memex8_envelope(
        slice_mod.Memex8Slice(
            name="s",
            memory_ids=["x"],
            base_url="http://sender:8080",
        ),
        to_peer="agent_receiver_zzz",
        sender_identity=sender_ident,
    )
    # Install a meeting with sender's id, public key matching.
    _setup_active_meeting(sender_id, "http://sender:8080")
    store = meetings.Meetings().load()
    m = store.get(sender_id)
    m.peer_public_key = sender_ident.public_key_b64
    store.save()
    # Mock the memex8 client to say the memory is private on refetch.
    with mock.patch.object(memex8_client_mod, "Memex8Client") as mc:
        mc.return_value.fetch_memex8_slice.side_effect = (
            memex8_client_mod.Memex8ClientError("memory 'x' is not visibility=public")
        )
        out = tools_mod.handle_receive_memex8({"envelope": env})
    assert "Refused" in out
    assert "not visibility=public" in out
