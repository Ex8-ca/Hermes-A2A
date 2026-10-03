"""Unit tests for the identity primitive.

Identity in v0.1.x is just a hash of the normalized URL. We verify
that the same URL always produces the same ID, that normalization
strips noise, and that fingerprints are stable and short.
"""

from __future__ import annotations

import pytest

from plugins.a2a_bridge import identity


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("http://example.com", "http://example.com"),
            ("HTTP://Example.COM", "http://example.com"),
            ("http://example.com/", "http://example.com"),
            ("https://example.com:443/", "https://example.com"),
            ("http://example.com:80/", "http://example.com"),
            ("http://example.com:8080/", "http://example.com:8080"),
            ("https://example.com/path/?q=1#frag", "https://example.com/path?q=1"),
            # Userinfo is stripped — credentials are not part of identity.
            # Two agents that differ only in userinfo are the same agent.
            ("http://user:pass@example.com", "http://example.com"),
        ],
    )
    def test_normalization(self, raw: str, expected: str) -> None:
        assert identity.normalize_url(raw) == expected

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            identity.normalize_url("")

    def test_no_host_raises(self) -> None:
        with pytest.raises(ValueError):
            identity.normalize_url("http://")


class TestAgentIdFor:
    def test_format(self) -> None:
        aid = identity.agent_id_for("http://example.com:9900")
        assert aid.startswith("agent_")
        # agent_ + 16 hex chars
        assert len(aid) == len("agent_") + 16
        int(aid.removeprefix("agent_"), 16)  # valid hex

    def test_stable(self) -> None:
        a = identity.agent_id_for("http://example.com:9900")
        b = identity.agent_id_for("http://example.com:9900")
        assert a == b

    def test_normalization_collapses(self) -> None:
        a = identity.agent_id_for("http://example.com:9900/")
        b = identity.agent_id_for("HTTP://Example.COM:9900")
        assert a == b

    def test_different_urls_different_ids(self) -> None:
        a = identity.agent_id_for("http://a.example.com:9900")
        b = identity.agent_id_for("http://b.example.com:9900")
        assert a != b

    def test_port_matters_when_non_default(self) -> None:
        a = identity.agent_id_for("http://example.com:8080")
        b = identity.agent_id_for("http://example.com:8081")
        assert a != b

    def test_path_matters(self) -> None:
        a = identity.agent_id_for("http://example.com/agent1")
        b = identity.agent_id_for("http://example.com/agent2")
        assert a != b

    def test_query_string_matters(self) -> None:
        a = identity.agent_id_for("http://example.com/?v=1")
        b = identity.agent_id_for("http://example.com/?v=2")
        assert a != b

    def test_custom_prefix(self) -> None:
        aid = identity.agent_id_for("http://example.com", prefix="bot_")
        assert aid.startswith("bot_")


class TestFingerprintUrl:
    def test_format(self) -> None:
        fp = identity.fingerprint_url("http://example.com:9900")
        # 4-4-4 hex groups
        parts = fp.split("-")
        assert len(parts) == 3
        for p in parts:
            assert len(p) == 4
            int(p, 16)  # valid hex

    def test_stable(self) -> None:
        a = identity.fingerprint_url("http://example.com:9900")
        b = identity.fingerprint_url("http://example.com:9900")
        assert a == b

    def test_normalization_collapses(self) -> None:
        a = identity.fingerprint_url("http://example.com:9900/")
        b = identity.fingerprint_url("HTTP://Example.COM:9900")
        assert a == b
