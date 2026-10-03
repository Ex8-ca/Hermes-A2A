"""Unit tests for the public-marking policy (allowlist + frontmatter).

We exercise the two-layer visibility model end-to-end:
  * Default deny for unmarked content.
  * Frontmatter that says DENY overrides the allowlist.
  * Allowlist + frontmatter must agree for sharing to be allowed.
  * public_approved requires the peer in approved_peers.
  * public_all is permissive (open by default unless
    approved_peers is non-empty).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.a2a_bridge import policy


# ── Level parsing ──────────────────────────────────────────────────────


class TestLevel:
    def test_parse_known(self) -> None:
        assert policy.parse_level("public_all") == policy.Level.PUBLIC_ALL
        assert policy.parse_level("public_approved") == policy.Level.PUBLIC_APPROVED
        assert policy.parse_level("deny") == policy.Level.DENY

    def test_parse_case_insensitive(self) -> None:
        assert policy.parse_level("PUBLIC_ALL") == policy.Level.PUBLIC_ALL
        assert policy.parse_level("Public_Approved") == policy.Level.PUBLIC_APPROVED

    def test_parse_unknown_is_deny(self) -> None:
        assert policy.parse_level("") == policy.Level.DENY
        assert policy.parse_level("garbage") == policy.Level.DENY
        assert policy.parse_level(None) == policy.Level.DENY  # type: ignore[arg-type]

    def test_is_shareable(self) -> None:
        assert policy.Level.PUBLIC_ALL.is_shareable()
        assert policy.Level.PUBLIC_APPROVED.is_shareable()
        assert not policy.Level.DENY.is_shareable()


# ── Allowlist loading ──────────────────────────────────────────────────


@pytest.fixture
def allowlist_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path / "a2a_bridge" / "public.yaml"


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestLoadAllowlist:
    def test_missing_file_is_empty_default(self, allowlist_path: Path) -> None:
        al = policy.load_allowlist(allowlist_path)
        assert al.slices == []
        assert al.default_visibility == policy.Level.DENY
        assert al.approved_peers == []
        assert al.diagnostics == []
        assert al.path == allowlist_path

    def test_parses_minimal(self, allowlist_path: Path) -> None:
        _write(allowlist_path, "default_visibility: public_all\n")
        al = policy.load_allowlist(allowlist_path)
        assert al.default_visibility == policy.Level.PUBLIC_ALL
        assert al.diagnostics == []

    def test_parses_full(self, allowlist_path: Path) -> None:
        _write(allowlist_path, (
            "default_visibility: deny\n"
            "approved_peers:\n"
            "  - agent_aaaaaaaa\n"
            "  - agent_bbbbbbbb\n"
            "slices:\n"
            "  memories:\n"
            "    - name: alpha\n"
            "      path: MEMORY.md\n"
            "      heading_anchor: '## 2026-10-02'\n"
            "      level: public_all\n"
            "    - name: beta\n"
            "      path: MEMORY.md\n"
            "      level: public_approved\n"
            "      approved_peers:\n"
            "        - agent_cccccccc\n"
            "  skills:\n"
            "    - name: cool-md\n"
            "      path: skills/cool-md\n"
            "      level: public_all\n"
        ))
        al = policy.load_allowlist(allowlist_path)
        assert al.default_visibility == policy.Level.DENY
        assert al.approved_peers == ["agent_aaaaaaaa", "agent_bbbbbbbb"]
        assert {s.name for s in al.slices} == {"alpha", "beta", "cool-md"}
        alpha = al.slice_by_name("alpha")
        assert alpha is not None
        assert alpha.kind == "memory"
        assert alpha.heading_anchor == "## 2026-10-02"
        assert alpha.level == policy.Level.PUBLIC_ALL
        beta = al.slice_by_name("beta")
        assert beta is not None
        assert beta.level == policy.Level.PUBLIC_APPROVED
        assert beta.approved_peers == ["agent_cccccccc"]
        assert al.diagnostics == []

    def test_diagnostic_on_bad_yaml(self, allowlist_path: Path) -> None:
        _write(allowlist_path, "this is: not: valid: yaml: [")
        al = policy.load_allowlist(allowlist_path)
        assert any("could not parse" in d for d in al.diagnostics)

    def test_diagnostic_on_non_mapping(self, allowlist_path: Path) -> None:
        _write(allowlist_path, "- just\n- a\n- list\n")
        al = policy.load_allowlist(allowlist_path)
        assert any("top-level must be" in d for d in al.diagnostics)

    def test_diagnostic_on_missing_name(self, allowlist_path: Path) -> None:
        _write(allowlist_path, (
            "slices:\n"
            "  memories:\n"
            "    - path: MEMORY.md\n"  # no name
        ))
        al = policy.load_allowlist(allowlist_path)
        assert any("missing 'name'" in d for d in al.diagnostics)


# ── Save round-trip ────────────────────────────────────────────────────


class TestSaveAllowlist:
    def test_round_trip(self, allowlist_path: Path) -> None:
        original = policy.Allowlist(
            default_visibility=policy.Level.PUBLIC_ALL,
            slices=[
                policy.Slice(
                    name="alpha", kind="memory", path="MEMORY.md",
                    level=policy.Level.PUBLIC_ALL,
                ),
                policy.Slice(
                    name="beta", kind="memory", path="MEMORY.md",
                    level=policy.Level.PUBLIC_APPROVED,
                    approved_peers=["agent_xxxxxxxx"],
                ),
                policy.Slice(
                    name="cool-md", kind="skill", path="skills/cool-md",
                    level=policy.Level.PUBLIC_ALL,
                ),
            ],
            approved_peers=["agent_aaaaaaaa", "agent_bbbbbbbb"],
        )
        policy.save_allowlist(original, allowlist_path)
        loaded = policy.load_allowlist(allowlist_path)
        assert loaded.default_visibility == policy.Level.PUBLIC_ALL
        assert loaded.approved_peers == ["agent_aaaaaaaa", "agent_bbbbbbbb"]
        assert {s.name for s in loaded.slices} == {"alpha", "beta", "cool-md"}


# ── Frontmatter scanning ───────────────────────────────────────────────


class TestScanFrontmatter:
    def test_yaml_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text(
            "---\nname: cool\nvisibility: public_all\n---\nbody\n",
            encoding="utf-8",
        )
        fm = policy.scan_frontmatter(p)
        assert fm is not None
        assert fm.level == policy.Level.PUBLIC_ALL
        assert fm.source == "yaml"
        assert fm.raw.get("name") == "cool"

    def test_html_comment(self, tmp_path: Path) -> None:
        p = tmp_path / "MEMORY.md"
        p.write_text(
            "Some text\n\n<!-- hermes:visibility=public_approved -->\n\nMore text\n",
            encoding="utf-8",
        )
        fm = policy.scan_frontmatter(p)
        assert fm is not None
        assert fm.level == policy.Level.PUBLIC_APPROVED
        assert fm.source == "html"

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "MEMORY.md"
        p.write_text("Just plain text.\n", encoding="utf-8")
        assert policy.scan_frontmatter(p) is None

    def test_unknown_value_is_deny(self, tmp_path: Path) -> None:
        p = tmp_path / "MEMORY.md"
        p.write_text("<!-- hermes:visibility=publicish -->\n", encoding="utf-8")
        fm = policy.scan_frontmatter(p)
        assert fm is not None
        assert fm.level == policy.Level.DENY

    def test_missing_file(self) -> None:
        assert policy.scan_frontmatter(Path("/nonexistent/path")) is None


# ── Resolution ─────────────────────────────────────────────────────────


class TestResolveShare:
    def test_unknown_slice_denied(self) -> None:
        al = policy.Allowlist()
        ok, reason = policy.resolve_share(al, peer_id="agent_x", slice_name="nope")
        assert not ok
        assert "not in the central allowlist" in reason

    def test_deny_level_denied(self) -> None:
        al = policy.Allowlist(slices=[
            policy.Slice(name="x", kind="memory", path="MEMORY.md", level=policy.Level.DENY),
        ])
        ok, _ = policy.resolve_share(al, peer_id="agent_x", slice_name="x")
        assert not ok

    def test_public_all_open_when_no_approved_peers(self) -> None:
        al = policy.Allowlist(
            approved_peers=[],
            slices=[policy.Slice(name="x", kind="memory", path="MEMORY.md", level=policy.Level.PUBLIC_ALL)],
        )
        ok, reason = policy.resolve_share(al, peer_id="agent_x", slice_name="x")
        assert ok
        assert "shareable" in reason

    def test_public_all_with_approved_peers_filters(self) -> None:
        al = policy.Allowlist(
            approved_peers=["agent_approved"],
            slices=[policy.Slice(name="x", kind="memory", path="MEMORY.md", level=policy.Level.PUBLIC_ALL)],
        )
        ok, _ = policy.resolve_share(al, peer_id="agent_random", slice_name="x")
        assert not ok
        ok, _ = policy.resolve_share(al, peer_id="agent_approved", slice_name="x")
        assert ok

    def test_public_approved_requires_peer_in_list(self) -> None:
        al = policy.Allowlist(
            slices=[policy.Slice(
                name="x", kind="memory", path="MEMORY.md",
                level=policy.Level.PUBLIC_APPROVED,
                approved_peers=["agent_special"],
            )],
        )
        ok, _ = policy.resolve_share(al, peer_id="agent_other", slice_name="x")
        assert not ok
        ok, _ = policy.resolve_share(al, peer_id="agent_special", slice_name="x")
        assert ok

    def test_public_approved_global_list_helps(self) -> None:
        al = policy.Allowlist(
            approved_peers=["agent_global_approved"],
            slices=[policy.Slice(
                name="x", kind="memory", path="MEMORY.md",
                level=policy.Level.PUBLIC_APPROVED,
            )],
        )
        ok, _ = policy.resolve_share(al, peer_id="agent_global_approved", slice_name="x")
        assert ok

    def test_data_side_deny_overrides(self) -> None:
        al = policy.Allowlist(
            slices=[policy.Slice(name="x", kind="memory", path="MEMORY.md", level=policy.Level.PUBLIC_ALL)],
        )
        fm = policy.Frontmatter(level=policy.Level.DENY)
        ok, reason = policy.resolve_share(al, peer_id="agent_x", slice_name="x", frontmatter=fm)
        assert not ok
        assert "refusing to override" in reason

    def test_data_side_agree(self) -> None:
        al = policy.Allowlist(
            slices=[policy.Slice(name="x", kind="memory", path="MEMORY.md", level=policy.Level.PUBLIC_ALL)],
        )
        fm = policy.Frontmatter(level=policy.Level.PUBLIC_ALL)
        ok, _ = policy.resolve_share(al, peer_id="agent_x", slice_name="x", frontmatter=fm)
        assert ok


# ── List shareable ────────────────────────────────────────────────────


class TestListShareable:
    def test_returns_per_slice_decision(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Create two memory files: one with public_all frontmatter,
        # one without (so policy layer says nothing about it).
        (tmp_path / "MEMORY.md").write_text(
            "<!-- hermes:visibility=public_all -->\n## Project Alpha\n",
            encoding="utf-8",
        )
        (tmp_path / "OTHER.md").write_text("No frontmatter here.\n", encoding="utf-8")
        al = policy.Allowlist(
            slices=[
                policy.Slice(name="alpha", kind="memory", path="MEMORY.md", level=policy.Level.PUBLIC_ALL),
                policy.Slice(name="other", kind="memory", path="OTHER.md", level=policy.Level.PUBLIC_ALL),
            ],
        )
        results = policy.list_shareable(al, peer_id="agent_x", memories=[], skills=[])
        # Map by slice name for easier assertion
        by_name = {r[0].name: r for r in results}
        # alpha has matching frontmatter, no policy conflict → shareable
        assert by_name["alpha"][2] is True
        # other has no frontmatter but policy says public_all → still
        # shareable (frontmatter absent is treated as "not specified",
        # so the policy layer is the only one in play)
        assert by_name["other"][2] is True
