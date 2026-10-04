#!/usr/bin/env python3
"""Operator helper: read the local Tailscale client and surface MagicDNS names.

Wraps ``tailscale status --json`` (the operator's *local* tailscaled is the
source of truth for MagicDNS hostnames — it talks to the coordination server
and resolves the local hostname's fully-qualified MagicDNS name).

The directory's ``submit.js`` already accepts ``http://<host>.ts.net:9900/...``
URLs without a liveness probe (the v0.3.3 ``isPrivateOrLoopbackHost()``
extension in ``directory/pages/functions/_validate.js``). What's missing on
the operator side is tooling to discover the local MagicDNS name and put it
into the ``agent_card_url`` field of a signed submission. This helper does
the discovery; ``make_submission.py --auto-tailscale`` is the integration.

JSON schema (relevant subset, from ``tailscale status --json``):

    {
      "MagicDNSSuffix": "taila6e2e.ts.net",
      "Self": {
        "HostName":     "minisforum-desktop",          # short hostname
        "DNSName":      "minisforum-desktop.taila6e2e.ts.net.",   # FQDN, trailing dot
        "OS":           "linux",
        "TailscaleIPs": ["100.88.26.20", "fd7a:115c:a1e0::..."],
        "Online":       true
      },
      "Peer": {                                         # keyed by NodeID
        "<NodeID>": {
          "HostName":     "ai5080",
          "DNSName":      "ai5080.taila6e2e.ts.net.",
          "OS":           "linux",
          "TailscaleIPs": ["100.117.6.105", ...],
          "Online":       true,
          "LastSeen":     "2026-10-03T..."              # only when Offline
        },
        ...
      }
    }

Usage::

    # Show the local host's MagicDNS name (FQDN, trailing dot stripped):
    python3 discover_tailscale.py --self

    # List all online peers (one per line, aligned columns):
    python3 discover_tailscale.py --peers

    # Default (no args): one-line summary (self + peer count + tailnet):
    python3 discover_tailscale.py

    # Machine-readable JSON of the filtered status:
    python3 discover_tailscale.py --json

    # Refresh and cache the filtered status to ~/.hermes/.tailscale-cache.json
    # (mode 0600) so other tools (make_submission.py --auto-tailscale) can
    # read it without re-spawning tailscale:
    python3 discover_tailscale.py --refresh-cache

No external deps. Standard library only (subprocess, json, pathlib, os).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TAILSCALE_BIN = "tailscale"
STATUS_TIMEOUT_SECONDS = 5
DEFAULT_CACHE_PATH = Path.home() / ".hermes" / ".tailscale-cache.json"


class TailscaleError(Exception):
    """The tailscale client is unreachable or returned bad data.

    The CLI exits non-zero on this; ``make_submission.py --auto-tailscale``
    propagates the exit code so its caller sees the same error.
    """


def _run_status() -> dict[str, Any]:
    """Call ``tailscale status --json`` and parse the result.

    Raises TailscaleError if tailscale is missing, fails, or returns
    unparsable JSON. The caller decides what to print.
    """
    try:
        proc = subprocess.run(
            [TAILSCALE_BIN, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=STATUS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise TailscaleError(
            f"{TAILSCALE_BIN!r} not found on PATH. Install Tailscale "
            f"(on Arch/Omarchy: `sudo pacman -S tailscale`) and bring it up "
            f"with `sudo tailscale up` before using --auto-tailscale."
        )
    except subprocess.TimeoutExpired:
        raise TailscaleError(
            f"{TAILSCALE_BIN} status --json timed out after "
            f"{STATUS_TIMEOUT_SECONDS}s. Is tailscaled hung?"
        )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        raise TailscaleError(
            f"{TAILSCALE_BIN} status --json failed (exit {proc.returncode}): "
            f"{stderr}"
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise TailscaleError(
            f"{TAILSCALE_BIN} status --json returned invalid JSON: {e}"
        )


def _strip_trailing_dot(dns_name: str) -> str:
    """DNS FQDNs in tailscale status end with a trailing dot; strip it
    for human-facing output. Empty string passes through."""
    return dns_name[:-1] if dns_name.endswith(".") else dns_name


def _self_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Return the interesting Self fields in a small shape."""
    self_block = status.get("Self") or {}
    return {
        "HostName":     self_block.get("HostName", ""),
        "DNSName":      _strip_trailing_dot(self_block.get("DNSName", "")),
        "OS":           self_block.get("OS", ""),
        "TailscaleIPs": list(self_block.get("TailscaleIPs") or []),
        "Online":       bool(self_block.get("Online", False)),
    }


def _peer_summaries(status: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """Return peer summaries.

    mode: "online"  — only online peers
          "offline" — only offline peers (with LastSeen if present)
          "all"     — both, sorted by HostName

    Each entry has HostName, DNSName (trailing dot stripped), OS,
    TailscaleIPs, Online, and LastSeen (offline peers only — RFC3339
    UTC string). Peers are returned in deterministic order: by HostName
    for stability across runs.
    """
    if mode not in ("online", "offline", "all"):
        raise ValueError(f"invalid peer mode: {mode!r}")
    out: list[dict[str, Any]] = []
    peers = status.get("Peer") or {}
    for _node_id, p in peers.items():
        online = bool(p.get("Online", False))
        if mode == "online" and not online:
            continue
        if mode == "offline" and online:
            continue
        entry: dict[str, Any] = {
            "HostName":     p.get("HostName", ""),
            "DNSName":      _strip_trailing_dot(p.get("DNSName", "")),
            "OS":           p.get("OS", ""),
            "TailscaleIPs": list(p.get("TailscaleIPs") or []),
            "Online":       online,
        }
        if not online and p.get("LastSeen"):
            entry["LastSeen"] = p.get("LastSeen")
        out.append(entry)
    out.sort(key=lambda e: e["HostName"])
    return out


def _make_filtered_status(status: dict[str, Any]) -> dict[str, Any]:
    """Build the filtered status dict used by --json and --refresh-cache.

    Stripped of the noisy fields (TxBytes, RxBytes, PeerAPIURL, Relay, etc.)
    so the cached file is small and human-readable.
    """
    return {
        "MagicDNSSuffix": status.get("MagicDNSSuffix", ""),
        "Self": _self_summary(status),
        "Peers": {
            "online":  _peer_summaries(status, mode="online"),
            "offline": _peer_summaries(status, mode="offline"),
        },
    }


def _print_self(status: dict[str, Any]) -> None:
    """Print just the FQDN (trailing dot stripped) for shell consumption."""
    s = _self_summary(status)
    print(s["DNSName"])


def _print_peers(status: dict[str, Any]) -> None:
    """Print online peers in aligned columns.

    Columns: dns, ip4, os, status. Peers are sorted by HostName.
    """
    peers = _peer_summaries(status, mode="online")
    if not peers:
        print("(no online peers)", file=sys.stderr)
        return

    rows: list[tuple[str, str, str, str]] = []
    for p in peers:
        # Use the first IPv4 IPv6; ignore v6 for the column (operators
        # care about the v4 for plain HTTP). Falls back to v6.
        v4 = next((ip for ip in p["TailscaleIPs"] if "." in ip), "")
        if not v4 and p["TailscaleIPs"]:
            v4 = p["TailscaleIPs"][0]
        rows.append((p["DNSName"], v4, p["OS"], "online" if p["Online"] else "offline"))

    w_dns = max(len(r[0]) for r in rows)
    w_ip = max(len(r[1]) for r in rows)
    w_os = max(len(r[2]) for r in rows)
    for dns, ip, os_name, status_str in rows:
        print(f"{dns:<{w_dns}}   {ip:<{w_ip}}   {os_name:<{w_os}}   {status_str}")


def _print_summary(status: dict[str, Any]) -> None:
    """Default no-args view: one short line per fact."""
    s = _self_summary(status)
    suffix = status.get("MagicDNSSuffix", "")
    online_peers = _peer_summaries(status, mode="online")
    offline_peers = _peer_summaries(status, mode="offline")
    online_self = "yes" if s["Online"] else "no"
    total = len(online_peers) + len(offline_peers)
    print(f"self:    {s['DNSName']}  ({s['HostName']}, {s['OS']}, online={online_self})")
    print(f"tailnet: {suffix}")
    print(f"peers:   {len(online_peers)} online, {len(offline_peers)} offline, "
          f"{total} total")


def _print_json(status: dict[str, Any]) -> None:
    """Print the filtered status as pretty JSON."""
    print(json.dumps(_make_filtered_status(status), indent=2, sort_keys=True))


def _write_cache(status: dict[str, Any], path: Path) -> None:
    """Atomically write the filtered status to `path` with mode 0600.

    Atomic write: write to ``path.tmp``, chmod 0600, os.replace. Pre-existing
    files are not re-chmodmed (operators may have set different perms
    deliberately — same convention as delete_entry.py).
    """
    payload = _make_filtered_status(status)
    payload["cached_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--self", action="store_true",
        help="Print the local host's MagicDNS FQDN (trailing dot stripped) and exit.",
    )
    g.add_argument(
        "--peers", action="store_true",
        help="List online peers, one per line (DNSName, IPv4, OS, status).",
    )
    g.add_argument(
        "--json", action="store_true",
        help="Print the filtered status as pretty JSON.",
    )
    g.add_argument(
        "--refresh-cache", action="store_true",
        help="Refresh ~/.hermes/.tailscale-cache.json (mode 0600) with the "
             "filtered status, then print the cache path. The cache is the "
             "file make_submission.py --auto-tailscale reads.",
    )
    p.add_argument(
        "--cache-path", type=Path, default=DEFAULT_CACHE_PATH,
        help=f"Path to write the cache (default: {DEFAULT_CACHE_PATH}). "
             "Only used with --refresh-cache.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ap = _build_argparser()
    args = ap.parse_args(argv)

    try:
        status = _run_status()
    except TailscaleError as e:
        print(f"tailscale: {e}", file=sys.stderr)
        return 1

    if args.self:
        _print_self(status)
    elif args.peers:
        _print_peers(status)
    elif args.json:
        _print_json(status)
    elif args.refresh_cache:
        _write_cache(status, args.cache_path)
        print(str(args.cache_path))
    else:
        _print_summary(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())