"""Unit tests for the a2a_bridge_shareable dry-run helper.

The helper resolves a peer via the identity primitive, loads the
central allowlist, and reports which declared slices are shareable
with that peer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.a2a_bridge import identity, policy
from plugins.a2a_bridge.tools import handle_shareable


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _write_allowlist(home: Path, content: str) -> Path:
    p = home / "a2a_bridge" / "public.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestHandleShareable:
    def test_missing_peer_url(self, hermes_home: Path) -> None:
        r = handle_shareable({})
        assert "Error" in r
        assert "peer_url" in r

    def test_invalid_peer_url(self, hermes_home: Path) -> None:
        r = handle_shareable({"peer_url": "not a url"})
        assert "Error" in r
        assert "invalid" in r.lower() or "no host" in r.lower()

    def test_no_slices_guides_user(self, hermes_home: Path) -> None:
        r = handle_shareable({"peer_url": "http://other.example.com:9900"})
        assert "No slices declared" in r
        assert "ROADMAP" in r

    def test_diagnostics_surfaced(self, hermes_home: Path) -> None:
        _write_allowlist(hermes_home, "this is: not: valid: yaml: [")
        r = handle_shareable({"peer_url": "http://other.example.com:9900"})
        assert "diagnostics" in r
        assert "could not parse" in r

    def test_shareable_with_approved_peer(
        self, hermes_home: Path
    ) -> None:
        # Create a memory file with matching frontmatter
        mem = hermes_home / "MEMORY.md"
        mem.write_text(
            "<!-- hermes:visibility=public_all -->\n## Project Alpha\n",
            encoding="utf-8",
        )
        _write_allowlist(hermes_home, (
            "default_visibility: deny\n"
            "slices:\n"
            "  memories:\n"
            "    - name: alpha\n"
            "      path: MEMORY.md\n"
            "      level: public_all\n"
        ))
        r = handle_shareable({"peer_url": "http://other.example.com:9900"})
        assert "alpha" in r
        assert "shareable" in r
        # The peer_id should appear too
        peer_id = identity.agent_id_for("http://other.example.com:9900")
        assert peer_id in r

    def test_blocked_slice_surfaces_reason(
        self, hermes_home: Path
    ) -> None:
        _write_allowlist(hermes_home, (
            "default_visibility: deny\n"
            "slices:\n"
            "  memories:\n"
            "    - name: private-notes\n"
            "      path: MEMORY.md\n"
            "      level: deny\n"
        ))
        r = handle_shareable({"peer_url": "http://other.example.com:9900"})
        assert "private-notes" in r
        assert "blocked" in r

    def test_agent_id_accepted_as_input(
        self, hermes_home: Path
    ) -> None:
        # If the user passes an agent_id directly, use it as-is.
        _write_allowlist(hermes_home, (
            "default_visibility: public_all\n"
            "slices:\n"
            "  memories:\n"
            "    - name: anything\n"
            "      path: MEMORY.md\n"
            "      level: public_all\n"
        ))
        r = handle_shareable({"peer_url": "agent_8f3a7c2d9b1e4f5a"})
        assert "agent_8f3a7c2d9b1e4f5a" in r
        assert "anything" in r
