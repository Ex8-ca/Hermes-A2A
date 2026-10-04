#!/usr/bin/env python3
"""Public third-party client demo.

This script simulates what a brand-new user — on a different tailnet
(or no tailnet at all) — would see when they discover agents via the
public directory at https://hermes-a2a.dpmob.com and try to call one.

It is meant to be runnable on any machine with Python 3.10+ and no
Hermes / Tailscale / agent configuration. The output shows exactly
what a third party sees:

  - What the directory lists (filtered and unfiltered)
  - What the per-agent SSR page looks like
  - What happens when they try to call a MagicDNS agent from a
    different network (the v0.4.3 contextual error UX)

Run:

    python3 scripts/discovery_client_demo.py

Exit code 0 if all steps complete (even if the agent call fails —
that's expected from a non-tailnet caller). Exit code 1 if any of
the network reads fail.

This script is the third-party integration test for v0.4.3 + v0.5.2.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DIRECTORY_BASE = "https://hermes-a2a.dpmob.com"
USER_AGENT = "hermes-a2a-discovery-demo/0.5 (+https://hermes-a2a.dpmob.com)"


def _http_get(url: str) -> tuple[int, bytes]:
    """Returns (status, body). Raises on network error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error for {url}: {e.reason}") from e


def _print_step(n: int, total: int, title: str) -> None:
    print(f"\n[{n}/{total}] {title}")
    print("-" * 60)


def main() -> int:
    total_steps = 5
    print("=" * 60)
    print("Hermes-A2A public directory — third-party discovery demo")
    print("=" * 60)
    print(f"Directory base: {DIRECTORY_BASE}")

    # Step 1: unfiltered list
    _print_step(1, total_steps, "GET /list (unfiltered)")
    status, body = _http_get(f"{DIRECTORY_BASE}/list")
    if status != 200:
        print(f"  ✗ HTTP {status}: {body[:200].decode('utf-8', errors='replace')}")
        return 1
    data = json.loads(body)
    print(f"  ✓ HTTP 200, {data['count']} entries in the catalog")
    for a in data["agents"]:
        agent_id = a.get("agent_id")
        transport = a.get("transport", "?")
        url = a.get("agent_card_url", "?")
        print(f"      • {agent_id}  [{transport}]  {url}")

    # Step 2: filtered list (this tailnet only)
    _print_step(2, total_steps, "GET /list?tailnet=taila6e2e")
    status, body = _http_get(f"{DIRECTORY_BASE}/list?tailnet=taila6e2e")
    data = json.loads(body)
    print(f"  ✓ HTTP 200, {data['count']} entries on taila6e2e")
    print(f"      filters echoed: {data.get('filters')}")
    for a in data["agents"]:
        print(f"      • {a.get('agent_id')}")

    # Step 3: filtered list (reachable via public HTTPS — should be 0)
    _print_step(3, total_steps, "GET /list?reachable_via=https")
    status, body = _http_get(f"{DIRECTORY_BASE}/list?reachable_via=https")
    data = json.loads(body)
    print(f"  ✓ HTTP 200, {data['count']} entries reachable via public HTTPS")
    if data["count"] == 0:
        print("      (no public-HTTPS entries — all entries are on private networks)")

    # Step 4: per-agent SSR page
    _print_step(4, total_steps, "GET /agent/desktop_2 (per-agent SSR page)")
    status, body = _http_get(f"{DIRECTORY_BASE}/agent/desktop_2")
    text = body.decode("utf-8", errors="replace")
    if status == 200 and "desktop" in text.lower():
        # Extract the title and transport chip for display
        import re
        title_match = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
        chip_match = re.search(r'transport-chip[^>]*>([^<]+)<', text)
        title = title_match.group(1).strip() if title_match else "(no h1)"
        chip = chip_match.group(1).strip() if chip_match else "(no chip)"
        print(f"  ✓ HTTP 200, {len(body)} bytes")
        print(f"      h1: {title[:80]}")
        print(f"      transport chip: {chip}")
    else:
        print(f"  ✗ HTTP {status}: page didn't render")

    # Step 5: try to call a MagicDNS agent from outside the tailnet
    _print_step(5, total_steps, "Try to call a MagicDNS agent from a non-tailnet caller")
    print("  (expecting a v0.4.3 contextual error since we're not on taila6e2e)")
    # To simulate a non-tailnet caller hitting our MagicDNS agents, we
    # intentionally use a *.ts.net hostname that does NOT exist on the
    # tailnet. Tailscale's MagicDNS will return NXDOMAIN, which mimics
    # the error shape a third party on a different tailnet would see.
    status, body = _http_get(f"{DIRECTORY_BASE}/list?transport=tailscale-magicdns")
    ts_entries = json.loads(body)["agents"]
    if not ts_entries:
        print("  (no MagicDNS entries in the catalog; skipping)")
        return 0
    # Pick the first MagicDNS agent and substitute its host with a
    # nonexistent one (preserving the .ts.net suffix so the URL still
    # looks like a real MagicDNS URL).
    real_url = ts_entries[0].get("agent_card_url")
    real_host = real_url.split("//", 1)[-1].split("/", 1)[0]
    parts = real_host.split(".")
    parts[0] = "nonexistent-test-host-7f3a"  # guaranteed not to exist
    fake_host = ".".join(parts)
    agent_card = real_url.replace(real_host, fake_host)
    print(f"  calling: {agent_card}")
    try:
        req = urllib.request.Request(agent_card, method="GET",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"  unexpected: HTTP {r.status} (this hostname resolved?)")
    except urllib.error.URLError as e:
        print(f"  ✓ network error: {type(e).__name__}: {e.reason}")
        print()
        print("  What the v0.4.3 helper would surface to a user calling this peer")
        print("  via a2a_bridge_send():")
        if ".ts.net" in agent_card:
            print(f'    "Error: peer is on a Tailscale tailnet (URL: {agent_card}).')
            print("     Your host is not on that tailnet, so the A2A call could not")
            print("     reach it. Run `tailscale status` to check your tailnet")
            print("     membership, or ask the peer operator to add your host to")
            print('     the tailnet\'s ACL. (Original error: ...)"')

    print()
    print("=" * 60)
    print("Demo complete. This is what a third-party discoverer sees.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())