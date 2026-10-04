"""Tests for the meeting handshake: build, parse, sign, verify."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from plugins.a2a_bridge import handshake, keyring


@pytest.fixture
def ident() -> keyring.Identity:
    return keyring._generate()


class TestBuildIntroduce:
    def test_format(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="share memories + skills",
            local_identity=ident,
        )
        assert env["method"] == "introduce"
        assert env["from"] == ident.agent_id
        assert env["public_key"] == ident.public_key_b64
        assert env["intent"] == "share memories + skills"
        assert "signature" in env
        # consent_until is ISO 8601
        datetime.fromisoformat(env["consent_until"].replace("Z", "+00:00"))

    def test_signature_verifies(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        # Manually verify the signature using the verifier.
        from plugins.a2a_bridge import canonical

        message = canonical.canonical_bytes(env)
        assert keyring.Identity.verify(env["public_key"], message, env["signature"])


class TestBuildIntroduceAck:
    def test_format(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce_ack(
            agent_card_url="https://example.com:9900",
            granted=["read_public"],
            local_identity=ident,
        )
        assert env["method"] == "introduce_ack"
        assert env["granted"] == ["read_public"]
        assert "signature" in env


class TestParseIncoming:
    def test_valid_introduce(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        verified = handshake.parse_incoming(env)
        assert "signature" not in verified
        assert verified["from"] == ident.agent_id

    def test_valid_introduce_ack(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce_ack(
            agent_card_url="https://example.com:9900",
            granted=["read_public"],
            local_identity=ident,
        )
        verified = handshake.parse_incoming(env)
        assert verified["method"] == "introduce_ack"
        assert verified["granted"] == ["read_public"]

    def test_rejects_unknown_method(self) -> None:
        with pytest.raises(handshake.HandshakeError, match="unexpected method"):
            handshake.parse_incoming({"method": "garbage", "from": "x"})

    def test_rejects_missing_field(self) -> None:
        with pytest.raises(handshake.HandshakeError, match="missing required field"):
            handshake.parse_incoming({
                "method": "introduce",
                "from": "x",
                # missing agent_card_url, public_key, consent_until, signature
            })

    def test_rejects_spoofed_from(self, ident: keyring.Identity) -> None:
        # Build a valid envelope, then claim to be a different agent_id.
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        env["from"] = "agent_aaaaaaaaaaaaaaab"  # not the key's fingerprint
        with pytest.raises(handshake.HandshakeError, match="does not match"):
            handshake.parse_incoming(env)

    def test_rejects_tampered_signature(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        # Mutate a non-signature field; signature now invalid.
        env["intent"] = "actually malicious"
        with pytest.raises(handshake.HandshakeError, match="signature did not verify"):
            handshake.parse_incoming(env)

    def test_rejects_tampered_signature_field(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        env["signature"] = "AAAA" + env["signature"][4:]
        with pytest.raises(handshake.HandshakeError):
            handshake.parse_incoming(env)

    def test_rejects_missing_prefix_in_public_key(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        env["public_key"] = "not-prefixed:" + env["public_key"].removeprefix("ed25519:")
        with pytest.raises(handshake.HandshakeError):
            handshake.parse_incoming(env)


class TestToMeetingRecord:
    def test_introduce_record(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
        )
        verified = handshake.parse_incoming(env)
        record = handshake.to_meeting_record(verified, our_url="https://us.example.com:9900")
        assert record["agent_id"] == ident.agent_id
        assert record["peer_url"] == "https://example.com:9900"
        assert record["peer_public_key"] == ident.public_key_b64
        assert record["intent"] == "hi"
        # introduce envelope has no granted; the caller's override wins
        assert record["granted"] == []

    def test_introduce_ack_record(self, ident: keyring.Identity) -> None:
        env = handshake.build_introduce_ack(
            agent_card_url="https://example.com:9900",
            granted=["read_public", "write_public"],
            local_identity=ident,
        )
        verified = handshake.parse_incoming(env)
        record = handshake.to_meeting_record(verified, our_url="https://us.example.com:9900")
        assert record["granted"] == ["read_public", "write_public"]


class TestRoundTrip:
    def test_full_introduce_then_ack(self, ident: keyring.Identity) -> None:
        # Two independent identities. We use a second freshly-generated
        # one to simulate the "responder" being a different agent.
        b = keyring._generate()
        a_intro = handshake.build_introduce(
            agent_card_url="https://a.example.com:9900",
            intent="share public slices",
            local_identity=ident,
        )
        # A's owner says: meet B with read_public. B verifies and replies.
        verified = handshake.parse_incoming(a_intro)
        assert verified["from"] == ident.agent_id
        b_ack = handshake.build_introduce_ack(
            agent_card_url="https://b.example.com:9900",
            granted=["read_public"],
            local_identity=b,
        )
        verified_ack = handshake.parse_incoming(b_ack)
        assert verified_ack["granted"] == ["read_public"]
        assert verified_ack["from"] == b.agent_id

    def test_replay_window_stale_envelope_rejected(self, ident: keyring.Identity) -> None:
        # Capture an envelope at t=0, then advance the receiver's clock
        # past max_age_seconds; the same envelope must reject.
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
            now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        # Receiver's clock has advanced 6 minutes; the envelope is now
        # 360s old, past the 300s default.
        with pytest.raises(handshake.HandshakeError, match="expired"):
            handshake.parse_incoming(
                env, now=lambda: datetime(2026, 1, 1, 0, 6, tzinfo=timezone.utc)
            )

    def test_replay_window_future_dated_envelope_rejected(self, ident: keyring.Identity) -> None:
        # Capture an envelope with a clock 10 minutes in the future
        # (sender clock skew). Receiver's clock is current; the future
        # date is past the 300s default.
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        )
        with pytest.raises(handshake.HandshakeError, match="not-yet-valid"):
            handshake.parse_incoming(
                env, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
            )

    def test_replay_window_accepts_within_tolerance(self, ident: keyring.Identity) -> None:
        # Within 300s of the receiver's clock the envelope is fine.
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc),  # 4m skew
        )
        verified = handshake.parse_incoming(
            env, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        assert verified["from"] == ident.agent_id

    def test_replay_window_custom_max_age(self, ident: keyring.Identity) -> None:
        # Operators can extend the window for slow networks.
        env = handshake.build_introduce(
            agent_card_url="https://example.com:9900",
            intent="hi",
            local_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        )
        # 30 minutes old, default 300s = rejected
        with pytest.raises(handshake.HandshakeError, match="expired"):
            handshake.parse_incoming(
                env,
                max_age_seconds=60,
                now=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
            )
        # ...but accepted if we set max_age=3600
        verified = handshake.parse_incoming(
            env,
            max_age_seconds=3600,
            now=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        )
        assert verified["from"] == ident.agent_id

