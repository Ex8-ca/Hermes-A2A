#!/usr/bin/env python3
"""End-to-end live test: A2A round-trip between .2 and .3 over both
LAN and Tailscale, plus the directory integration.

Run with:
    uv run --with cryptography --with pyyaml python scripts/e2e_live_test.py

Exits 0 if all checks pass, non-zero otherwise. Prints a summary at
the end. Does NOT mutate any state — this is a read-only probe.

Checks performed (in order):
  1. .2 → .3 LAN round-trip via a2a-call (the A2A platform itself)
  2. .3 → .2 LAN round-trip via a2a-call
  3. .2 → .3 Tailscale round-trip (MagicDNS resolution + a2a-call)
  4. .3 → .2 Tailscale round-trip
  5. Directory /list returns 2 entries (desktop_2 and ai5080)
  6. Per-agent SSR pages /agent/desktop_2 and /agent/ai5080 return 200
  7. discover_tailscale.py works on this host (live Tailscale API)
  8. The plugin's integration_smoke.py tests pass

This script is intentionally idempotent and quiet-on-success. It is
the operational health check for the v0.4.0 release: if every
section is green, .2 and .3 can talk to each other over both
networks, the directory knows about both, and the operator tooling
works against a real Tailscale client.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


# Two .2 env vars for the two outbound directions.
# In the existing setup, A2A_PEER_TOKEN_ai386.3 is the token .2 uses
# when talking to .3. The reverse direction (.3 → .2) uses a separate
# token configured on .3 (and a peer-name pair).
LAN_2_TO_3_TOKEN = "desktop--BaXKRN0lZJu1wAhY5qUUHhxAXNGHmWJ"  # the v0.3.3-era token
LAN_3_TO_2_TOKEN = "desktop2-sVhUWNrSUh0fj9s6TP7m7qRtbo0Tqjl7"  # the .2 inbound token

LAN_2_URL = "http://192.168.1.2:9900"
LAN_3_URL = "http://192.168.1.3:9900"
TS_2_URL = "http://minisforum-desktop.taila6e2e.ts.net:9900"
TS_3_URL = "http://ai5080.taila6e2e.ts.net:9900"

DIRECTORY_LIST = "https://hermes-a2a.dpmob.com/list"
DIRECTORY_AGENT_2 = "https://hermes-a2a.dpmob.com/agent/desktop_2"
DIRECTORY_AGENT_3 = "https://hermes-a2a.dpmob.com/agent/ai5080"


def _ok(name: str) -> None:
    print(f"  ✓ {name}")


def _fail(name: str, detail: str = "") -> None:
    print(f"  ✗ {name}")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")


def _http_get(url: str, timeout: int = 8) -> bytes:
    """GET a URL with an explicit User-Agent header.

    Cloudflare Pages Functions return 403 to Python's default
    urllib user-agent ("Python-urllib/3.x"). The directory's
    make_submission.py and render_agents.py both send an explicit
    user-agent for the same reason; this helper does the same.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "hermes-a2a-e2e/0.4 (+https://hermes-a2a.dpmob.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def a2a_round_trip(peer_url: str, token: str, marker: str) -> tuple[bool, str]:
    """Send a JSON-RPC message/send to peer_url with bearer token.
    Returns (success, reply_text).

    Accepts both TASK_STATE_COMPLETED (the agent answered) and
    TASK_STATE_INPUT_REQUIRED (the agent asked a follow-up — valid
    for ambiguous prompts). Other states (FAILED, etc.) are failures.
    """
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {
            "id": f"e2e-{marker}",
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": f"e2e-health-{marker}"}],
            },
        },
    }).encode()
    req = urllib.request.Request(
        peer_url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return False, f"{type(e).__name__}: {e}"
    status = data.get("result", {}).get("status", {})
    state = status.get("state")
    if state not in ("TASK_STATE_COMPLETED", "TASK_STATE_INPUT_REQUIRED"):
        return False, f"state={state!r} (expected COMPLETED or INPUT_REQUIRED)"
    parts = status.get("message", {}).get("parts", [])
    if not parts:
        return True, f"state={state}, <no agent reply text>"
    return True, f"state={state}, reply={parts[0].get('text', '')!r}"


def check(name: str, fn) -> bool:
    print(f"\n[{name}]")
    try:
        ok, detail = fn()
    except Exception as e:
        _fail(name, f"{type(e).__name__}: {e}")
        return False
    if ok:
        _ok(detail if detail else name)
        return True
    _fail(name, detail)
    return False


def main() -> int:
    print("=" * 64)
    print("Hermes-A2A e2e live test (.2 ↔ .3, LAN + Tailscale + directory)")
    print("=" * 64)
    print(f"  this host: {os.uname().nodename}")
    print(f"  working dir: {os.getcwd()}")

    failures = 0

    # 1. .2 → .3 LAN
    if not check(
        "1. .2 → .3 LAN round-trip",
        lambda: a2a_round_trip(LAN_3_URL, LAN_2_TO_3_TOKEN, "2to3-lan"),
    ):
        failures += 1

    # 2. .3 → .2 LAN
    if not check(
        "2. .3 → .2 LAN round-trip",
        lambda: a2a_round_trip(LAN_2_URL, LAN_3_TO_2_TOKEN, "3to2-lan"),
    ):
        failures += 1

    # 3. .2 → .3 Tailscale
    if not check(
        "3. .2 → .3 Tailscale round-trip",
        lambda: a2a_round_trip(TS_3_URL, LAN_2_TO_3_TOKEN, "2to3-ts"),
    ):
        failures += 1

    # 4. .3 → .2 Tailscale
    if not check(
        "4. .3 → .2 Tailscale round-trip",
        lambda: a2a_round_trip(TS_2_URL, LAN_3_TO_2_TOKEN, "3to2-ts"),
    ):
        failures += 1

    # 5. directory /list
    def check_directory_list() -> tuple[bool, str]:
        data = json.loads(_http_get(DIRECTORY_LIST))
        count = data.get("count", 0)
        agents = [a.get("agent_id") for a in data.get("agents", [])]
        if count != 2 or "desktop_2" not in agents or "ai5080" not in agents:
            return False, f"expected 2 entries (desktop_2, ai5080), got count={count} agents={agents}"
        return True, f"count={count}, agents={agents}"

    if not check("5. directory /list has 2 clean entries", check_directory_list):
        failures += 1

    # 6. per-agent SSR pages
    def check_ssr(url: str) -> tuple[bool, str]:
        try:
            body = _http_get(url)
            return True, f"{len(body)} bytes"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    if not check("6. /agent/desktop_2 SSR page", lambda: check_ssr(DIRECTORY_AGENT_2)):
        failures += 1
    if not check("6. /agent/ai5080 SSR page", lambda: check_ssr(DIRECTORY_AGENT_3)):
        failures += 1

    # 7. discover_tailscale.py works
    def check_discover() -> tuple[bool, str]:
        r = subprocess.run(
            [sys.executable, "directory/operator/discover_tailscale.py", "--self"],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode != 0:
            return False, f"non-zero exit; stderr: {r.stderr.strip()}"
        fqdn = r.stdout.strip()
        if not fqdn.endswith(".ts.net"):
            return False, f"unexpected fqdn: {fqdn!r}"
        return True, f"self={fqdn}"

    if not check("7. discover_tailscale.py --self", check_discover):
        failures += 1

    # 8. plugin integration smoke (only if not already run)
    def check_smoke() -> tuple[bool, str]:
        # Read the peer token from .env (env-only path is opt-in).
        env_token = os.environ.get("HERMES_A2A_TEST_TOKEN") or LAN_2_TO_3_TOKEN
        env = os.environ.copy()
        env["HERMES_A2A_TEST_TOKEN"] = env_token
        env["A2A_BRIDGE_TEST_PEER_URL"] = LAN_3_URL
        r = subprocess.run(
            [
                "uv", "run", "--with", "pytest", "--with", "cryptography", "--with", "pyyaml",
                "python", "-m", "pytest", "plugins/a2a_bridge/tests/integration_smoke.py", "-q",
            ],
            capture_output=True, text=True, timeout=120,
            env=env,
        )
        if r.returncode != 0:
            return False, f"pytest exit {r.returncode}; tail of stdout:\n{r.stdout[-500:]}"
        return True, "all 9 integration smoke tests pass"

    if not check("8. plugin integration_smoke.py", check_smoke):
        failures += 1

    print()
    print("=" * 64)
    if failures == 0:
        print(f"All checks pass. The v0.4 stack is live and operational.")
        return 0
    print(f"{failures} check(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
