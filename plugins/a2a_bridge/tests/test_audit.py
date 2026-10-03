"""Unit tests for the audit-log reader.

We point HERMES_HOME at a tempdir and write a synthetic JSONL log.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest

from plugins.a2a_bridge import audit


@dataclass
class FakeAuditHome:
    """Set HERMES_HOME to a tempdir; let tests write a synthetic audit log."""
    path: Path

    def write(self, rows: list[dict]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")


@pytest.fixture
def fake_audit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAuditHome]:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield FakeAuditHome(path=tmp_path / "a2a_audit.jsonl")


def _row(ts: int, direction: str, peer: str, summary: str, task_id: str) -> Dict[str, Any]:
    return {
        "ts": float(ts),
        "direction": direction,
        "peer": peer,
        "task_id": task_id,
        "summary": summary,
    }


class TestReadAudit:
    def test_no_log_returns_empty(self, fake_audit_home: Dict[str, Any]) -> None:
        assert audit.read_audit() == []

    def test_returns_all_unfiltered(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(1, "outbound", "ai386.3", "PONG", "task-1"),
            _row(2, "inbound", "desktop", "HELLO", "task-2"),
        ])
        rows = audit.read_audit()
        assert len(rows) == 2

    def test_filters_by_peer(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(1, "outbound", "ai386.3", "x", "t1"),
            _row(2, "outbound", "other-peer", "y", "t2"),
            _row(3, "outbound", "ai386.3", "z", "t3"),
        ])
        rows = audit.read_audit(peer="ai386.3")
        assert len(rows) == 2
        assert all("ai386.3" in r["peer"] for r in rows)

    def test_filters_by_direction(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(1, "outbound", "ai386.3", "x", "t1"),
            _row(2, "inbound", "ai386.3", "y", "t2"),
            _row(3, "outbound", "ai386.3", "z", "t3"),
        ])
        rows = audit.read_audit(direction="outbound")
        assert len(rows) == 2
        assert all(r["direction"] == "outbound" for r in rows)

    def test_last_caps_to_n_newest(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(i, "outbound", "ai386.3", f"msg-{i}", f"t-{i}") for i in range(50)
        ])
        rows = audit.read_audit(last=5)
        assert len(rows) == 5
        assert rows[-1]["summary"] == "msg-49"

    def test_skips_malformed_lines(self, fake_audit_home: FakeAuditHome) -> None:
        fake_audit_home.path.write_text(
            json.dumps(_row(1, "outbound", "ai386.3", "good", "t1")) + "\n"
            + "{ not valid\n"
            + "\n"
            + json.dumps(_row(2, "outbound", "ai386.3", "good2", "t2")) + "\n",
            encoding="utf-8",
        )
        rows = audit.read_audit()
        assert len(rows) == 2


class TestFormat:
    def test_empty_returns_marker(self) -> None:
        assert audit.format_audit_table([]) == "(no matching audit entries)"

    def test_non_empty_returns_table(self) -> None:
        rows = [_row(1700000000, "outbound", "ai386.3", "PONG", "task-1")]
        out = audit.format_audit_table(rows)
        assert "| time" in out
        assert "outbound" in out
        assert "ai386.3" in out
        assert "PONG" in out


class TestListPeers:
    def test_aggregates_by_peer(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(100, "outbound", "ai386.3", "a", "t1"),
            _row(200, "outbound", "ai386.3", "b", "t2"),
            _row(300, "inbound", "desktop", "c", "t3"),
            _row(400, "outbound", "other-peer", "d", "t4"),
        ])
        rows = audit.list_peers()
        assert {r["peer"] for r in rows} == {"ai386.3", "desktop", "other-peer"}
        ai = next(r for r in rows if r["peer"] == "ai386.3")
        assert ai["count"] == 2

    def test_sort_by_most_recent(self, fake_audit_home: Dict[str, Any]) -> None:
        fake_audit_home.write([
            _row(100, "outbound", "ai386.3", "old", "t1"),
            _row(999, "outbound", "desktop", "new", "t2"),
        ])
        rows = audit.list_peers()
        assert rows[0]["peer"] == "desktop"

    def test_handles_missing_ts(self, fake_audit_home: FakeAuditHome) -> None:
        # No ts at all
        fake_audit_home.path.write_text(
            json.dumps({"direction": "outbound", "peer": "ai386.3", "summary": "x", "task_id": "t1"}) + "\n",
            encoding="utf-8",
        )
        rows = audit.list_peers()
        assert rows[0]["peer"] == "ai386.3"
        assert rows[0]["last_seen"] == "?"

    def test_empty_marker(self) -> None:
        assert audit.format_peer_list([]) == "(no peers seen yet — make a call first)"