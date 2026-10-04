"""Tests for directory/operator/discover_tailscale.py.

The CLI shells out to ``tailscale status --json`` and parses the result.
Tests mock ``subprocess.run`` so no live tailscale install is required.

Coverage:

- tailscale missing from PATH: exits non-zero with a clear error.
- tailscale returns non-zero: exits non-zero, surfaces stderr.
- tailscale returns valid JSON: --self prints the FQDN without the trailing
  dot; --peers lists online peers; --json prints the filtered dict.
- tailscale returns JSON with offline peers: --peers filters them out;
  --json includes them with LastSeen.
- --refresh-cache writes a mode-0600 JSON file with the filtered status.
- The TailscaleError class is exposed so callers (make_submission.py)
  can catch it cleanly.
"""

from __future__ import annotations

import errno
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discover_tailscale  # type: ignore  # noqa: E402


# A representative ``tailscale status --json`` payload covering: self,
# online peer, offline peer with LastSeen. Mirrors the real schema.
SAMPLE_STATUS: dict[str, Any] = {
    "Version": "1.102.3",
    "MagicDNSSuffix": "taila6e2e.ts.net",
    "Self": {
        "HostName": "minisforum-desktop",
        "DNSName": "minisforum-desktop.taila6e2e.ts.net.",
        "OS": "linux",
        "TailscaleIPs": ["100.88.26.20", "fd7a:115c:a1e0::532c:1a15"],
        "Online": True,
    },
    "Peer": {
        "n1234": {
            "HostName": "ai5080",
            "DNSName": "ai5080.taila6e2e.ts.net.",
            "OS": "linux",
            "TailscaleIPs": ["100.117.6.105", "fd7a:115c:a1e0::b01:669"],
            "Online": True,
        },
        "n5678": {
            "HostName": "offline-host",
            "DNSName": "offline-host.taila6e2e.ts.net.",
            "OS": "macos",
            "TailscaleIPs": ["100.99.99.99"],
            "Online": False,
            "LastSeen": "2026-09-29T00:02:55.1Z",
        },
    },
}


def _fake_proc(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a mock CompletedProcess-like object for subprocess.run."""
    proc = mock.Mock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _patch_run(monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0,
               stdout: str = "", stderr: str = "",
               raise_file_not_found: bool = False,
               raise_timeout: bool = False,
               json_value: Any | None = None) -> mock.Mock:
    """Patch subprocess.run inside discover_tailscale with a fake.

    - raise_file_not_found: subprocess.run raises FileNotFoundError.
    - raise_timeout: subprocess.run raises TimeoutExpired.
    - json_value: parsed JSON for stdout (overrides `stdout`).
    """
    if raise_file_not_found:
        def fake_run(*args, **kwargs):
            raise FileNotFoundError(errno.ENOENT, "tailscale")
        patched = mock.Mock(side_effect=fake_run)
    elif raise_timeout:
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="tailscale", timeout=5)
        patched = mock.Mock(side_effect=fake_run)
    elif json_value is not None:
        out_str = json.dumps(json_value)
        proc = _fake_proc(returncode, stdout=out_str, stderr=stderr)
        patched = mock.Mock(return_value=proc)
    else:
        proc = _fake_proc(returncode, stdout=stdout, stderr=stderr)
        patched = mock.Mock(return_value=proc)

    monkeypatch.setattr(discover_tailscale.subprocess, "run", patched)
    return patched


class TestTailscaleMissing:
    def test_not_on_path(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _patch_run(monkeypatch, raise_file_not_found=True)
        rc = discover_tailscale.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found on PATH" in err
        assert "tailscale" in err

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _patch_run(monkeypatch, raise_timeout=True)
        rc = discover_tailscale.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "timed out" in err


class TestTailscaleNonzeroExit:
    def test_nonzero_exit_surfaces_stderr(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _patch_run(monkeypatch, returncode=2,
                   stderr="tailscale: not logged in\n")
        rc = discover_tailscale.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "not logged in" in err
        assert "exit 2" in err

    def test_nonzero_with_empty_stderr(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _patch_run(monkeypatch, returncode=1, stderr="")
        rc = discover_tailscale.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "(no stderr)" in err

    def test_invalid_json(self, monkeypatch: pytest.MonkeyPatch, capsys):
        _patch_run(monkeypatch, returncode=0, stdout="{not json at all")
        rc = discover_tailscale.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "invalid JSON" in err


class TestSelfFlag:
    def test_self_prints_fqdn_without_trailing_dot(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main(["--self"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "minisforum-desktop.taila6e2e.ts.net"
        assert not out.endswith(".")


class TestPeersFlag:
    def test_peers_lists_only_online(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main(["--peers"])
        assert rc == 0
        out = capsys.readouterr().out
        # Online peer shows up
        assert "ai5080.taila6e2e.ts.net" in out
        assert "100.117.6.105" in out
        assert "online" in out
        # Offline peer is filtered out
        assert "offline-host.taila6e2e.ts.net" not in out

    def test_peers_handles_empty(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        # No peers at all (e.g. a single-host tailnet)
        status = dict(SAMPLE_STATUS, Peer={})
        _patch_run(monkeypatch, json_value=status)
        rc = discover_tailscale.main(["--peers"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "no online peers" in err


class TestJsonFlag:
    def test_json_prints_filtered_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main(["--json"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["MagicDNSSuffix"] == "taila6e2e.ts.net"
        assert parsed["Self"]["DNSName"] == "minisforum-desktop.taila6e2e.ts.net"
        # Online and offline are separate buckets
        assert any(p["DNSName"].endswith("ai5080.taila6e2e.ts.net")
                   for p in parsed["Peers"]["online"])
        assert any(p["DNSName"].endswith("offline-host.taila6e2e.ts.net")
                   for p in parsed["Peers"]["offline"])
        # Offline peer carries LastSeen
        offline_peer = next(p for p in parsed["Peers"]["offline"]
                            if p["DNSName"].startswith("offline-host."))
        assert offline_peer["LastSeen"] == "2026-09-29T00:02:55.1Z"
        # Online peer does NOT carry LastSeen (none in sample)
        online_peer = next(p for p in parsed["Peers"]["online"]
                           if p["DNSName"].startswith("ai5080."))
        assert "LastSeen" not in online_peer
        # The noisy fields from raw tailscale status are stripped
        assert "Peer" not in parsed
        assert "TxBytes" not in json.dumps(parsed)


class TestDefaultSummary:
    def test_default_prints_summary(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "self:    minisforum-desktop.taila6e2e.ts.net" in out
        assert "tailnet: taila6e2e.ts.net" in out
        assert "1 online" in out
        assert "1 offline" in out
        assert "2 total" in out


class TestRefreshCache:
    def test_refresh_cache_writes_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ):
        cache_path = tmp_path / "tailscale-cache.json"
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main(["--refresh-cache", "--cache-path",
                                      str(cache_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert str(cache_path) in out  # script prints the cache path
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["MagicDNSSuffix"] == "taila6e2e.ts.net"
        assert data["Self"]["DNSName"] == "minisforum-desktop.taila6e2e.ts.net"
        assert "cached_at" in data
        # 0600 mode
        mode = stat.S_IMODE(cache_path.stat().st_mode)
        assert mode == 0o600

    def test_refresh_cache_creates_parent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        cache_path = tmp_path / "deep" / "nested" / "tailscale-cache.json"
        _patch_run(monkeypatch, json_value=SAMPLE_STATUS)
        rc = discover_tailscale.main(["--refresh-cache", "--cache-path",
                                      str(cache_path)])
        assert rc == 0
        assert cache_path.exists()