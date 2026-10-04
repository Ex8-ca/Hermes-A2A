"""Smoke test for ``make_submission.py --auto-tailscale``.

This is the single end-to-end test for the new flag. It temporarily
replaces ``directory/operator/discover_tailscale.py`` with a stub that
returns a known MagicDNS name so the test doesn't depend on a live
Tailscale install, then invokes ``make_submission.py --auto-tailscale``
as a real subprocess with ``--dry-run`` (so no POST hits the live
directory). The stub is restored on teardown.

Covers the full precedence rule:

  * ``--auto-tailscale`` alone: pre-fills both ``--card-url`` and ``--name``.
  * ``--auto-tailscale --card-url …``: explicit URL wins, stderr warns.
  * ``--auto-tailscale --name …``: explicit name wins, auto-fill is silent.
  * ``--auto-tailscale --port N``: ``--port`` overrides the default 9900.
  * No ``--auto-tailscale`` and no ``--name``: CLI errors with a clear message.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Sample MagicDNS name as it would appear in discover_tailscale.py --self
# (FQDN with trailing dot stripped).
SAMPLE_MAGICDNS = "minisforum-desktop.taila6e2e.ts.net"


STUB_DISCOVER = dedent(f"""\
    #!/usr/bin/env python3
    import sys
    if "--self" in sys.argv[1:]:
        print("{SAMPLE_MAGICDNS}")
        sys.exit(0)
    sys.exit(0)
""")


REAL_DISCOVER = ROOT / "discover_tailscale.py"


@pytest.fixture
def stubbed_discover():
    """Replace discover_tailscale.py with a stub for the test, restore on exit."""
    if not REAL_DISCOVER.exists():
        pytest.skip("discover_tailscale.py not found in operator/")
    backup = REAL_DISCOVER.with_suffix(".py.bak")
    shutil.copy(REAL_DISCOVER, backup)
    try:
        REAL_DISCOVER.write_text(STUB_DISCOVER)
        yield REAL_DISCOVER
    finally:
        shutil.move(str(backup), str(REAL_DISCOVER))


@pytest.fixture
def op_key(tmp_path: Path) -> Path:
    """Write a fake 32-byte ed25519 seed, base64-encoded, for the test."""
    raw = bytes(range(32))
    key_path = tmp_path / "op.key"
    key_path.write_text(base64.b64encode(raw).decode())
    return key_path


def _run_make_submission(op_key: Path, *extra: str) -> subprocess.CompletedProcess:
    """Invoke make_submission.py via a real subprocess with --dry-run."""
    return subprocess.run(
        [sys.executable, str(ROOT / "make_submission.py"),
         "--key-path", str(op_key),
         "--capabilities", "a2a_call",
         "--dry-run",
         *extra],
        capture_output=True, text=True, timeout=20,
    )


def test_auto_tailscale_end_to_end(
    op_key: Path, stubbed_discover: Path,
):
    """Exercise the full --auto-tailscale precedence matrix in one test.

    Happy path: ``--auto-tailscale`` alone fills both ``--card-url``
    and ``--name``. The signed envelope carries the auto-built MagicDNS
    URL and the ``<host> (operator)`` display name.

    Precedence rules:
      * ``--card-url`` wins over the auto-fill (with a stderr warning).
      * ``--name`` wins over the auto-fill (silent, no name message).
      * ``--port`` overrides the default 9900.

    Error path: omitting both ``--name`` and ``--auto-tailscale`` is a
    clear, actionable error.
    """
    expected_url = (
        "http://minisforum-desktop.taila6e2e.ts.net:9900/"
        ".well-known/agent-card.json"
    )

    # 1. Happy path: both fields auto-filled.
    proc = _run_make_submission(op_key, "--auto-tailscale")
    assert proc.returncode == 0, proc.stderr
    assert "auto-tailscale: card URL" in proc.stderr
    assert expected_url in proc.stderr
    assert "auto-tailscale: name" in proc.stderr
    assert "minisforum-desktop (operator)" in proc.stderr
    assert expected_url in proc.stdout
    assert '"name":"minisforum-desktop (operator)"' in proc.stdout

    # 2. --card-url wins (with warning); --name still auto-filled.
    proc = _run_make_submission(
        op_key, "--auto-tailscale",
        "--card-url", "https://example.com/custom",
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning: --auto-tailscale pre-fills" in proc.stderr
    assert "the explicit --card-url wins" in proc.stderr
    assert '"agent_card_url":"https://example.com/custom"' in proc.stdout
    assert "minisforum-desktop.taila6e2e.ts.net" not in proc.stdout
    # name was not explicit, so auto-fill still fires
    assert '"name":"minisforum-desktop (operator)"' in proc.stdout

    # 3. --name wins; auto-fill message for name is silent.
    proc = _run_make_submission(
        op_key, "--auto-tailscale", "--name", "explicit name wins",
    )
    assert proc.returncode == 0, proc.stderr
    assert '"name":"explicit name wins"' in proc.stdout
    assert "auto-tailscale: name" not in proc.stderr  # no auto-fill message

    # 4. --port overrides the default 9900.
    proc = _run_make_submission(op_key, "--auto-tailscale", "--port", "8888")
    assert proc.returncode == 0, proc.stderr
    assert ":8888/.well-known/agent-card.json" in proc.stdout
    assert ":9900/.well-known/agent-card.json" not in proc.stdout

    # 5. Error path: no --name, no --auto-tailscale.
    proc = _run_make_submission(op_key)
    assert proc.returncode != 0
    assert "--name is required" in proc.stderr
    assert "--auto-tailscale" in proc.stderr  # points operator at the new flag