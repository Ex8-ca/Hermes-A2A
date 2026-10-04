"""Tests for the slice transport."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from plugins.a2a_bridge import policy, slice as slice_mod
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
