"""Tests for the operator-rotation CLI.

The CLI manipulates the central ROOT_SYSTEM_POLICY file that the
directory's submit.js reads as a JSON-string env var. Operators are
listed in policy.approvers[] with name, ed25519_pubkey_b64, and an
added_at timestamp. The CLI supports --list, --add, --remove, and
writes an append-only audit log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure we can import the operator module
ROOT = Path(__file__).resolve().parent.parent / "operator"
sys.path.insert(0, str(ROOT))

import policy_rotate  # type: ignore  # noqa: E402


@pytest.fixture
def policy_path(tmp_path) -> Path:
    return tmp_path / "policy.json"


@pytest.fixture
def audit_path(tmp_path) -> Path:
    return tmp_path / "policy.audit.log"


@pytest.fixture
def fresh_policy(policy_path: Path) -> None:
    """Start with an empty allowlist (fail-closed default)."""
    policy_rotate.write_policy(
        policy_path,
        {"approvers": [], "version": 1},
    )


def _b64_key(seed: int) -> str:
    """A fake ed25519 public key (just 32 bytes of filler, base64'd).
    Production keys are 32 bytes; we don't need real ed25519 here —
    we're testing the policy-rotation CLI, not signature verification.
    """
    import base64
    return base64.b64encode(bytes([seed] * 32)).decode("ascii")


class TestListApprovers:
    def test_empty(self, policy_path, audit_path, fresh_policy):
        out = policy_rotate.list_approvers(policy_path)
        assert out == []

    def test_one(self, policy_path, audit_path, fresh_policy):
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        out = policy_rotate.list_approvers(policy_path)
        assert len(out) == 1
        assert out[0]["name"] == "alice"
        assert "added_at" in out[0]


class TestAddApprover:
    def test_add_appends(self, policy_path, audit_path, fresh_policy):
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        policy_rotate.add_approver(
            policy_path, audit_path, name="bob", pubkey_b64=_b64_key(2),
        )
        out = policy_rotate.list_approvers(policy_path)
        assert [a["name"] for a in out] == ["alice", "bob"]

    def test_add_duplicate_rejected(self, policy_path, audit_path, fresh_policy):
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        with pytest.raises(policy_rotate.PolicyError, match="already"):
            policy_rotate.add_approver(
                policy_path, audit_path, name="alice", pubkey_b64=_b64_key(2),
            )

    def test_audit_log_appended(self, policy_path, audit_path, fresh_policy):
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "add"
        assert entry["name"] == "alice"


class TestRemoveApprover:
    def test_remove(self, policy_path, audit_path, fresh_policy):
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        policy_rotate.add_approver(
            policy_path, audit_path, name="bob", pubkey_b64=_b64_key(2),
        )
        policy_rotate.remove_approver(policy_path, audit_path, name="alice")
        out = policy_rotate.list_approvers(policy_path)
        assert [a["name"] for a in out] == ["bob"]

    def test_remove_unknown_rejected(self, policy_path, audit_path, fresh_policy):
        with pytest.raises(policy_rotate.PolicyError, match="not found"):
            policy_rotate.remove_approver(policy_path, audit_path, name="ghost")

    def test_remove_last_rejected(self, policy_path, audit_path, fresh_policy):
        # Removing the last approver would leave the directory with an
        # empty allowlist, which the submit.js fails-closed. Refuse.
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        with pytest.raises(policy_rotate.PolicyError, match="empty"):
            policy_rotate.remove_approver(policy_path, audit_path, name="alice")


class TestReadWrite:
    def test_round_trip(self, policy_path, audit_path, fresh_policy, tmp_path):
        original = {
            "approvers": [
                {"name": "alice", "ed25519_pubkey_b64": _b64_key(1), "added_at": "2026-01-01T00:00:00Z"},
            ],
            "version": 1,
        }
        policy_rotate.write_policy(policy_path, original)
        out = policy_rotate.read_policy(policy_path)
        assert out == original

    def test_missing_file_returns_empty(self, policy_path, audit_path, fresh_policy, tmp_path):
        # Remove the file, ensure read returns a sensible default
        policy_path.unlink()
        out = policy_rotate.read_policy(policy_path)
        assert out["approvers"] == []
        assert out["version"] == 1


class TestAuditPermissions:
    def test_audit_log_mode(self, policy_path, audit_path, fresh_policy):
        """The audit log is sensitive (records who was added/removed when);
        it must be readable only by the owner (mode 0600)."""
        import stat
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        mode = stat.S_IMODE(audit_path.stat().st_mode)
        assert mode == 0o600

    def test_policy_file_mode(self, policy_path, audit_path, fresh_policy):
        """The policy file contains public keys (not secret), but we
        still want it not world-writable."""
        import stat
        policy_rotate.add_approver(
            policy_path, audit_path, name="alice", pubkey_b64=_b64_key(1),
        )
        mode = stat.S_IMODE(policy_path.stat().st_mode)
        # 0600 or 0644 are both fine; 0666 is not.
        assert mode & 0o002 == 0
