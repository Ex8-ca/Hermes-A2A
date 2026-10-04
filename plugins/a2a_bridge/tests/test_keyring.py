"""Tests for the ed25519 keyring.

We test:
  * generate / load round-trip
  * sign / verify round-trip
  * public_key_b64 form
  * agent_id stability
  * atomic save (file mode 0600)
  * corruption is detected (not silently accepted)
  * URL fallback works when no key is on disk
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest

from plugins.a2a_bridge import keyring


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


class TestGenerate:
    def test_load_or_create_creates_file(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        assert (fake_home / "a2a_bridge" / "identity.key").exists()
        assert len(ident.private_bytes) == 32
        assert len(ident.public_bytes) == 32

    def test_load_or_create_idempotent(self, fake_home: Path) -> None:
        a = keyring.load_or_create()
        b = keyring.load_or_create()
        assert a.agent_id == b.agent_id
        assert a.public_bytes == b.public_bytes

    def test_file_mode_0600(self, fake_home: Path) -> None:
        keyring.load_or_create()
        p = fake_home / "a2a_bridge" / "identity.key"
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_file_is_atomic_via_tmp(self, fake_home: Path) -> None:
        # On success, no .tmp file is left behind.
        keyring.load_or_create()
        leftover = list((fake_home / "a2a_bridge").glob("*.tmp"))
        assert leftover == []


class TestPublicKeyFormat:
    def test_format(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        s = ident.public_key_b64
        assert s.startswith("ed25519:")
        b64 = s.removeprefix("ed25519:")
        raw = base64.b64decode(b64)
        assert len(raw) == 32


class TestAgentId:
    def test_format(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        aid = ident.agent_id
        assert aid.startswith("agent_")
        assert len(aid) == len("agent_") + 16
        int(aid.removeprefix("agent_"), 16)

    def test_stable_across_loads(self, fake_home: Path) -> None:
        a = keyring.load_or_create()
        # Drop in-memory, reload from disk, agentId must match.
        del a
        b = keyring.load_or_create()
        assert b.agent_id == keyring.agent_id()


class TestSignVerify:
    def test_round_trip(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        sig = ident.sign(b"hello world")
        assert keyring.Identity.verify(ident.public_key_b64, b"hello world", sig)

    def test_verify_rejects_tampered_message(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        sig = ident.sign(b"hello world")
        assert not keyring.Identity.verify(ident.public_key_b64, b"hello WORLD", sig)

    def test_verify_rejects_bad_signature(self, fake_home: Path) -> None:
        # Manually construct a malformed (but base64-valid) signature of
        # the right length, so the verifier decodes it but the math fails.
        # 64 random bytes is overwhelmingly unlikely to verify as a real
        # ed25519 signature for any key.
        import secrets

        ident = keyring.load_or_create()
        msg = b"hello world"
        bad_sig = base64.b64encode(secrets.token_bytes(64)).decode()
        assert not keyring.Identity.verify(ident.public_key_b64, msg, bad_sig)

    def test_verify_rejects_missing_prefix(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        sig = ident.sign(b"hi")
        assert not keyring.Identity.verify("not-ed25519-prefix", b"hi", sig)

    def test_verify_rejects_garbage(self, fake_home: Path) -> None:
        assert not keyring.Identity.verify("ed25519:!!!", b"hi", "junk")

    def test_verify_rejects_wrong_length(self, fake_home: Path) -> None:
        # A 30-byte "public key" should be rejected, not crash.
        bad = "ed25519:" + base64.b64encode(b"x" * 30).decode()
        assert not keyring.Identity.verify(bad, b"hi", "junk")


class TestNoKey:
    def test_public_key_b64_is_none(self, fake_home: Path) -> None:
        assert keyring.public_key_b64() is None

    def test_agent_id_is_none(self, fake_home: Path) -> None:
        assert keyring.agent_id() is None

    def test_load_or_create_creates(self, fake_home: Path) -> None:
        # Even if no key exists, load_or_create must succeed.
        ident = keyring.load_or_create()
        assert ident.public_bytes

    def test_url_fallback_when_no_key(self, fake_home: Path) -> None:
        from plugins.a2a_bridge.identity import agent_id_for
        url = "http://example.com:9900"
        expected = agent_id_for(url)
        assert keyring.agent_id_or_url_fallback(url) == expected

    def test_url_fallback_ignored_when_key_exists(self, fake_home: Path) -> None:
        ident = keyring.load_or_create()
        # Even if the URL would yield a different ID, the key wins.
        assert keyring.agent_id_or_url_fallback("http://other.example.com") == ident.agent_id


class TestCorruption:
    def test_corrupt_file_treated_as_missing(self, fake_home: Path) -> None:
        p = fake_home / "a2a_bridge" / "identity.key"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not valid base64 !@#$", encoding="ascii")
        assert keyring.public_key_b64() is None
        # load_or_create should overwrite the corrupt file.
        ident = keyring.load_or_create()
        assert ident.public_bytes

    def test_wrong_length_treated_as_missing(self, fake_home: Path) -> None:
        p = fake_home / "a2a_bridge" / "identity.key"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(base64.b64encode(b"x" * 16).decode(), encoding="ascii")  # 16 not 32
        assert keyring.public_key_b64() is None
