"""Tests for the slice transport."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from plugins.a2a_bridge import handshake, policy, slice as slice_mod
from plugins.a2a_bridge.slice import Slice


@pytest.fixture
def sample_slice() -> Slice:
    return Slice(
        name="project-alpha",
        kind="memory",
        level=policy.Level.PUBLIC_ALL,
        contents=[{"heading": "## Project Alpha — 2026-10-02", "body": "Some notes about Alpha."}],
    )


class TestBuildEnvelope:
    def test_format(self, sample_slice: Slice) -> None:
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(sample_slice, to_peer="agent_zzzzzzzzzzzz", sender_identity=ident)
        assert env["kind"] == "memory_slice"
        assert env["from_peer"] == ident.agent_id
        assert env["from_public_key"] == ident.public_key_b64
        assert env["to_peer"] == "agent_zzzzzzzzzzzz"
        assert env["slice_name"] == "project-alpha"
        assert env["level"] == "public_all"
        assert env["contents"] == sample_slice.contents
        assert "signature" in env

    def test_signature_verifies(self, sample_slice: Slice) -> None:
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(sample_slice, to_peer="agent_zzz", sender_identity=ident)
        message = slice_mod.canonical.canonical_bytes(env)
        assert slice_mod.keyring.Identity.verify(env["from_public_key"], message, env["signature"])


class TestParseEnvelope:
    def test_valid(self, sample_slice: Slice) -> None:
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(sample_slice, to_peer="agent_zzz", sender_identity=ident)
        out = slice_mod.parse_envelope(env)
        assert out.name == "project-alpha"
        assert out.level == policy.Level.PUBLIC_ALL
        assert out.contents == sample_slice.contents

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(slice_mod.SliceError, match="unexpected kind"):
            slice_mod.parse_envelope({"kind": "not-a-slice", "from_peer": "x"})

    def test_rejects_missing_field(self) -> None:
        with pytest.raises(slice_mod.SliceError, match="missing required field"):
            slice_mod.parse_envelope({"kind": "memory_slice", "from_peer": "x"})

    def test_rejects_spoofed_from(self, sample_slice: Slice) -> None:
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(sample_slice, to_peer="agent_zzz", sender_identity=ident)
        env["from_peer"] = "agent_aaaaaaaaaaaaaaa"  # doesn't match the key
        with pytest.raises(slice_mod.SliceError, match="does not match"):
            slice_mod.parse_envelope(env)

    def test_rejects_tampered_contents(self, sample_slice: Slice) -> None:
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(sample_slice, to_peer="agent_zzz", sender_identity=ident)
        env["contents"].append({"heading": "sneaky", "body": "injected"})
        with pytest.raises(slice_mod.SliceError, match="signature"):
            slice_mod.parse_envelope(env)


class TestProvenance:
    def test_format(self) -> None:
        env = {
            "from_peer": "agent_8f3a7c2d9b1e4f5a",
            "from_public_key": "ed25519:" + "A" * 43,
            "task_id": "task-abc",
        }
        out = slice_mod.format_provenance(env)
        assert "<!-- a2a-bridge:" in out
        assert "source=agent_8f3a7c2d9b1e4f5a" in out
        assert "task=task-abc" in out


class TestEndToEnd:
    def test_two_identities_full_round_trip(self, sample_slice: Slice) -> None:
        a = slice_mod.keyring._generate()
        b = slice_mod.keyring._generate()

        # A composes and signs.
        env = slice_mod.build_envelope(sample_slice, to_peer=b.agent_id, sender_identity=a)

        # B receives and verifies. Sends back an "I got it" message.
        verified = slice_mod.parse_envelope(env)
        assert verified.name == "project-alpha"
        assert verified.level == policy.Level.PUBLIC_ALL

        # The provenance block references A, not B.
        prov = slice_mod.format_provenance(env)
        assert a.agent_id in prov
        assert b.agent_id not in prov


class TestReplayWindow:
    def test_stale_envelope_rejected(self, sample_slice: Slice) -> None:
        # Sender's clock is t=0; receiver's clock is t=6m (past the 300s window).
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(
            sample_slice,
            to_peer="agent_zzz",
            sender_identity=ident,
            now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        with pytest.raises(handshake.HandshakeError, match="expired"):
            slice_mod.parse_envelope(
                env, now=lambda: datetime(2026, 1, 1, 0, 6, tzinfo=timezone.utc)
            )

    def test_future_dated_envelope_rejected(self, sample_slice: Slice) -> None:
        # Sender's clock is 10 minutes in the future (clock skew). Receiver
        # is current. With the 300s default, the future-dated envelope
        # is rejected.
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(
            sample_slice,
            to_peer="agent_zzz",
            sender_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        )
        with pytest.raises(handshake.HandshakeError, match="not-yet-valid"):
            slice_mod.parse_envelope(
                env, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
            )

    def test_within_window_accepted(self, sample_slice: Slice) -> None:
        # 4 minutes of sender clock skew; receiver is current. Within
        # the 300s default; envelope is accepted.
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(
            sample_slice,
            to_peer="agent_zzz",
            sender_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc),
        )
        verified = slice_mod.parse_envelope(
            env, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        assert verified.name == "project-alpha"

    def test_custom_max_age(self, sample_slice: Slice) -> None:
        # 30 minutes old; the default 300s rejects, but max_age=3600 accepts.
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(
            sample_slice,
            to_peer="agent_zzz",
            sender_identity=ident,
            now=lambda: datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        )
        with pytest.raises(handshake.HandshakeError, match="expired"):
            slice_mod.parse_envelope(
                env,
                max_age_seconds=60,
                now=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
            )
        verified = slice_mod.parse_envelope(
            env,
            max_age_seconds=3600,
            now=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        )
        assert verified.name == "project-alpha"


class TestFetchContents:
    """v0.3: real slice fetching. The sender reads the slice from
    its HERMES_HOME and serializes it as a list of
    {heading, body} dicts that the receiver can re-write."""

    def test_fetch_whole_file(self, tmp_path) -> None:
        from plugins.a2a_bridge.slice import fetch_contents
        # Fixture: a MEMORY.md with three sections
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Project Alpha — 2026-10-02\n"
            "Some notes about Alpha.\n\n"
            "## Project Beta — 2026-10-03\n"
            "Beta work in progress.\n\n"
            "## Project Gamma — 2026-10-04\n"
            "Gamma is on hold.\n"
        )
        slice_ = Slice(
            name="all-memories",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            source_path="MEMORY.md",  # used by fetch_contents
            contents=[],
        )
        out = fetch_contents(slice_, home=tmp_path)
        # Three sections returned, each as a {heading, body} dict
        assert len(out) == 3
        assert out[0]["heading"].startswith("## Project Alpha")
        assert "Some notes about Alpha" in out[0]["body"]
        assert out[1]["heading"].startswith("## Project Beta")
        assert out[2]["heading"].startswith("## Project Gamma")

    def test_fetch_single_heading(self, tmp_path) -> None:
        from plugins.a2a_bridge.slice import fetch_contents
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Project Alpha — 2026-10-02\n"
            "Some notes about Alpha.\n\n"
            "## Project Beta — 2026-10-03\n"
            "Beta work in progress.\n"
        )
        slice_ = Slice(
            name="alpha-only",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            source_path="MEMORY.md",
            heading_anchor="Project Alpha",
            contents=[],
        )
        out = fetch_contents(slice_, home=tmp_path)
        # Only the Alpha section
        assert len(out) == 1
        assert "Project Alpha" in out[0]["heading"]
        assert "Some notes about Alpha" in out[0]["body"]
        assert "Beta" not in out[0]["heading"]

    def test_fetch_missing_file_raises(self, tmp_path) -> None:
        from plugins.a2a_bridge.slice import fetch_contents, SliceFetchError
        slice_ = Slice(
            name="ghost",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            source_path="nonexistent.md",
            contents=[],
        )
        with pytest.raises(SliceFetchError, match="not found"):
            fetch_contents(slice_, home=tmp_path)

    def test_fetch_escapes_home(self, tmp_path) -> None:
        from plugins.a2a_bridge.slice import fetch_contents, SliceFetchError
        slice_ = Slice(
            name="evil",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            source_path="../../../etc/passwd",
            contents=[],
        )
        with pytest.raises(SliceFetchError, match="escapes"):
            fetch_contents(slice_, home=tmp_path)

    def test_fetch_to_envelope_round_trip(self, tmp_path) -> None:
        """The full sender→receiver flow: fetch contents, build the
        envelope on the sender side, parse it on the receiver side,
        confirm the bytes match exactly."""
        from plugins.a2a_bridge.slice import fetch_contents

        # Sender's MEMORY.md
        memory = tmp_path / "MEMORY.md"
        memory.write_text(
            "## Project Alpha — 2026-10-02\n"
            "Original notes here.\n\n"
            "## Project Beta — 2026-10-03\n"
            "Beta notes.\n"
        )
        slice_ = Slice(
            name="alpha-and-beta",
            kind="memory",
            level=policy.Level.PUBLIC_ALL,
            source_path="MEMORY.md",
            contents=[],
        )
        contents = fetch_contents(slice_, home=tmp_path)
        # Build the envelope with the fetched contents
        ident = slice_mod.keyring._generate()
        env = slice_mod.build_envelope(
            Slice(
                name=slice_.name,
                kind=slice_.kind,
                level=slice_.level,
                contents=contents,
            ),
            to_peer="agent_other",
            sender_identity=ident,
        )
        # Round-trip: receiver parses, gets the same contents.
        verified = slice_mod.parse_envelope(env)
        assert len(verified.contents) == 2
        assert "Original notes here" in verified.contents[0]["body"]
        assert "Beta notes" in verified.contents[1]["body"]
