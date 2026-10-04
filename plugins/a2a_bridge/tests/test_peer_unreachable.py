"""Unit tests for the v0.4.3 peer-unreachable classifier.

The helper maps a generic network-error string from the Hermes A2A
platform's a2a_call tool to a contextual error that names the
network requirement (same tailnet, same LAN/VPN, etc.) when the
peer URL points at a Tailscale MagicDNS name or a private address.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add plugins/ to sys.path so the test can import the bridge directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from plugins.a2a_bridge.tools import (  # noqa: E402
    _classify_peer_unreachable,
    _is_private_or_loopback_url,
)


def test_tailscale_magicdns_with_dns_failure() -> None:
    raw = (
        "Error: call to 'ai5080.taila6e2e.ts.net' failed — "
        "urlopen error [Errno -2] Name or service not known"
    )
    out = _classify_peer_unreachable(
        "http://ai5080.taila6e2e.ts.net:9900/.well-known/agent-card.json",
        raw,
    )
    assert out is not None
    assert "Tailscale tailnet" in out
    assert "ai5080.taila6e2e.ts.net" in out
    assert "tailscale status" in out
    assert raw in out


def test_lan_ip_with_timeout() -> None:
    raw = "Error: call to '192.168.1.5' failed — urlopen error timed out"
    out = _classify_peer_unreachable(
        "http://192.168.1.5:9900/.well-known/agent-card.json",
        raw,
    )
    assert out is not None
    assert "private network" in out
    assert "192.168.1.5" in out
    assert ("LAN" in out or "tailnet" in out)
    assert raw in out


def test_public_url_with_dns_failure() -> None:
    raw = (
        "Error: call to 'example.com' failed — "
        "urlopen error [Errno -2] Name or service not known"
    )
    out = _classify_peer_unreachable("https://example.com/agent.json", raw)
    assert out is None


def test_successful_reply_passthrough() -> None:
    out = _classify_peer_unreachable(
        "http://ai5080.taila6e2e.ts.net:9900/.well-known/agent-card.json",
        "Hello, friend. [agent · ctx-1]\n\nReply text here.",
    )
    assert out is None


def test_auth_error_passthrough() -> None:
    raw = "Error: peer 'X' rejected auth (HTTP 401). Check the configured token."
    out = _classify_peer_unreachable(
        "http://ai5080.taila6e2e.ts.net:9900/.well-known/agent-card.json",
        raw,
    )
    assert out is None


def test_loopback_url_passes_through_isprivateorloopback() -> None:
    raw = (
        "Error: call to '127.0.0.1' failed — [Errno 111] Connection refused"
    )
    out = _classify_peer_unreachable("http://127.0.0.1:9900/agent.json", raw)
    assert out is not None
    assert "private network" in out
    assert "127.0.0.1" in out


def test_tailscale_cgnat_ip_is_lan() -> None:
    assert _is_private_or_loopback_url("http://100.88.26.20:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://100.64.0.0:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://100.127.255.255:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://100.63.255.255:9900/agent.json") == "public"
    assert _is_private_or_loopback_url("http://100.128.0.0:9900/agent.json") == "public"


def test_ipv6_private_addresses() -> None:
    assert _is_private_or_loopback_url("http://[fc00::1]:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://[fd00::1]:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://[fe80::1]:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://[::1]:9900/agent.json") == "lan"
    assert _is_private_or_loopback_url("http://[2001:db8::1]:9900/agent.json") == "public"
    assert _is_private_or_loopback_url("http://[2606:4700:4700::1111]:9900/agent.json") == "public"


def test_connection_refused_with_tailscale() -> None:
    raw = "Error: call to 'host.taila6e2e.ts.net' failed — Connection refused"
    out = _classify_peer_unreachable(
        "http://host.taila6e2e.ts.net:9900/agent.json", raw
    )
    assert out is not None
    assert "Tailscale" in out
