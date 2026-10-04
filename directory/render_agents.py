#!/usr/bin/env python3
"""Render per-agent profile pages from directory/pages/agent/_template.html.

Reads the live catalog from https://hermes-a2a.dpmob.com/list (or any
URL passed via --list-url) and produces one HTML file per agent in
directory/pages/agent/<agent_id>.html, baking the agent's data into
the HTML so the page is complete without JS. Re-run after every
submit so newly-added agents get their own page, and so updates to
existing agents propagate.

Usage:
    python3 render_agents.py
    python3 render_agents.py --list-url https://example.com/list
    python3 render_agents.py --pages-dir directory/pages

The actual rendering lives in directory/render.mjs (Node ESM). This
script is a thin CLI wrapper that fetches the catalog and shells out
to render_one.mjs once per page. Keeping the rendering in Node means
the same code runs in the Node test suite and at the operator's CLI
— there's no second implementation to drift.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


def fetch_list(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"user-agent": "hermes-a2a-renderer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def render_via_node(render_one: Path, template_path: str, entry: dict[str, Any], out_path: Path) -> None:
    """Run render_one.mjs on one entry; writes HTML to out_path."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(entry, f)
        entry_path = f.name
    try:
        subprocess.run(
            ["node", str(render_one), template_path, entry_path, str(out_path)],
            check=True, capture_output=True, text=True,
        )
    finally:
        os.unlink(entry_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-url", default="https://hermes-a2a.dpmob.com/list")
    ap.add_argument("--pages-dir", default="directory/pages")
    args = ap.parse_args()

    catalog = fetch_list(args.list_url)
    agents = catalog.get("agents", [])
    if not agents:
        print(f"WARN: /list returned 0 agents; nothing to render", file=sys.stderr)

    render_one = Path(__file__).parent / "render_one.mjs"
    if not render_one.exists():
        print(f"ERROR: render helper not found at {render_one}", file=sys.stderr)
        return 1

    template_path = str(Path(args.pages_dir) / "agent" / "_template.html")

    out_paths = []
    for entry in agents:
        agent_id = entry["agent_id"]
        out_path = Path(args.pages_dir) / "agent" / f"{agent_id}.html"
        render_via_node(render_one, template_path, entry, out_path)
        out_paths.append(out_path)
        print(f"wrote {out_path} ({entry.get('name', agent_id)})")

    print(f"done: {len(out_paths)} agent page(s) rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
