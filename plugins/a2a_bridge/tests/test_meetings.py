"""Tests for the meeting store."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from plugins.a2a_bridge.meetings import (
    Meeting,
    Meetings,
    default_consent_until,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _meeting(agent_id: str = "agent_aaaa", **kwargs) -> Meeting:
    defaults = dict(
        agent_id=agent_id,
        peer_url="https://peer.example.com:9900",
        peer_public_key="ed25519:" + "A" * 43,
        granted=["read_public"],
        intent="share memories + skills",
        established_at="2026-10-04T00:00:00Z",
        consent_until="2026-11-04T00:00:00Z",
        last_seen="2026-10-04T00:00:00Z",
        revoked=False,
    )
    defaults.update(kwargs)
    return Meeting(**defaults)


class TestLoadSave:
    def test_missing_file_is_empty(self, fake_home: Path) -> None:
        m = Meetings().load()
        assert m.all() == []
        assert m.path == fake_home / "a2a_bridge" / "meetings.json"

    def test_save_and_load_round_trip(self, fake_home: Path) -> None:
        m = Meetings().load()
        m.upsert(_meeting("agent_aaa"))
        m.upsert(_meeting("agent_bbb", peer_url="https://other.example.com:9900"))
        m.save()

        m2 = Meetings().load()
        assert {x.agent_id for x in m2.all()} == {"agent_aaa", "agent_bbb"}

    def test_corrupt_file_is_empty(self, fake_home: Path) -> None:
        p = fake_home / "a2a_bridge" / "meetings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not json", encoding="utf-8")
        m = Meetings().load()
        assert m.all() == []


class TestUpsert:
    def test_upsert_sets_established_at(self, fake_home: Path) -> None:
        m = Meetings().load()
        meeting = _meeting("agent_aaa", established_at="")
        m.upsert(meeting)
        assert meeting.established_at  # set by upsert

    def test_upsert_updates_last_seen(self, fake_home: Path) -> None:
        m = Meetings().load()
        m.upsert(_meeting("agent_aaa", last_seen="2020-01-01T00:00:00Z"))
        m.upsert(_meeting("agent_aaa", last_seen="2020-01-01T00:00:00Z"))
        out = m.get("agent_aaa")
        assert out.last_seen != "2020-01-01T00:00:00Z"


class TestIsActive:
    def test_active_when_in_future(self) -> None:
        m = _meeting(consent_until="2099-01-01T00:00:00Z")
        assert m.is_active()

    def test_inactive_when_past(self) -> None:
        m = _meeting(consent_until="2020-01-01T00:00:00Z")
        assert not m.is_active()

    def test_inactive_when_revoked(self) -> None:
        m = _meeting(consent_until="2099-01-01T00:00:00Z", revoked=True)
        assert not m.is_active()

    def test_inactive_when_no_consent_until(self) -> None:
        m = _meeting(consent_until="")
        assert not m.is_active()


class TestCan:
    def test_grants(self) -> None:
        m = _meeting(granted=["read_public", "write_public"])
        assert m.can("read_public")
        assert m.can("write_public")
        assert not m.can("delete")

    def test_empty_grants(self) -> None:
        m = _meeting(granted=[])
        assert not m.can("read_public")


class TestRevoke:
    def test_revoke_marks_revoked(self, fake_home: Path) -> None:
        m = Meetings().load()
        m.upsert(_meeting("agent_aaa"))
        m.save()
        m2 = Meetings().load()
        assert m2.revoke("agent_aaa")
        m2.save()
        m3 = Meetings().load()
        assert m3.get("agent_aaa").revoked

    def test_revoke_unknown(self, fake_home: Path) -> None:
        m = Meetings().load()
        assert not m.revoke("agent_unknown")

    def test_revoked_is_not_active(self) -> None:
        m = _meeting(revoked=True)
        assert not m.is_active()


class TestActive:
    def test_active_filters_expired(self, fake_home: Path) -> None:
        m = Meetings().load()
        m.upsert(_meeting("agent_active", consent_until="2099-01-01T00:00:00Z"))
        m.upsert(_meeting("agent_expired", consent_until="2020-01-01T00:00:00Z"))
        m.save()
        m2 = Meetings().load()
        ids = {x.agent_id for x in m2.active()}
        assert "agent_active" in ids
        assert "agent_expired" not in ids


class TestListForDisplay:
    def test_empty(self, fake_home: Path) -> None:
        m = Meetings().load()
        out = m.list_for_display()
        assert "no meetings" in out.lower()

    def test_table_includes_statuses(self, fake_home: Path) -> None:
        m = Meetings().load()
        m.upsert(_meeting("agent_active", consent_until="2099-01-01T00:00:00Z"))
        m.upsert(_meeting("agent_expired", consent_until="2020-01-01T00:00:00Z"))
        m.upsert(_meeting("agent_revoked", revoked=True))
        out = m.list_for_display()
        assert "active" in out
        assert "expired" in out
        assert "revoked" in out
