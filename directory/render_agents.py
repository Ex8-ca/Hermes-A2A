#!/usr/bin/env python3
"""Render per-agent profile pages from directory/pages/agent/_template.html.

Reads the live catalog from https://hermes-a2a.dpmob.com/list (or any
URL passed via --list-url) and produces one HTML file per agent in
directory/pages/agent/<agent_id>.html, baking the agent's id into the
template. Re-run after every submit so newly-added agents get their
own page.

Usage:
    python3 render_agents.py
    python3 render_agents.py --list-url https://example.com/list
    python3 render_agents.py --pages-dir directory/pages
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from typing import Any


def fetch_list(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"user-agent": "hermes-a2a-renderer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def render(template: str, entry: dict[str, Any]) -> str:
    """Bake the agent id into the template by replacing the URL-parsing
    JS with a literal constant. The rest of the template's runtime
    code (which fetches /list to populate the fields) is left intact."""
    agent_id = entry["agent_id"]
    safe_id = agent_id.replace("\\", "\\\\").replace('"', '\\"')
    old_block = (
        "    const id = decodeURIComponent(\n"
        "      location.pathname.replace(/^\\/agent\\//, \"\").replace(/\\.html$/, \"\")\n"
        "    );\n"
        "    document.getElementById(\"agent-id\").textContent = id;"
    )
    new_block = (
        '    // Agent id is baked in by the build (see render_agents.py).\n'
        f'    const id = "{safe_id}";\n'
        '    document.getElementById("agent-id").textContent = id;'
    )
    if old_block not in template:
        raise RuntimeError("template marker for agent_id not found — template changed?")
    out = template.replace(old_block, new_block, 1)
    # Switch the data source from the static seed to /list, so the
    # rendered page reflects whatever the catalog currently shows.
    out = out.replace('fetch("/agents.json")', 'fetch("/list")', 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-url", default="https://hermes-a2a.dpmob.com/list")
    ap.add_argument("--pages-dir", default="directory/pages")
    args = ap.parse_args()

    catalog = fetch_list(args.list_url)
    agents = catalog.get("agents", [])
    if not agents:
        print(f"WARN: /list returned 0 agents; nothing to render", file=sys.stderr)

    template_path = f"{args.pages_dir}/agent/_template.html"
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    out_paths = []
    for entry in agents:
        agent_id = entry["agent_id"]
        out_path = f"{args.pages_dir}/agent/{agent_id}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(render(template, entry))
        out_paths.append(out_path)
        print(f"wrote {out_path} ({entry.get('name', agent_id)})")

    print(f"done: {len(out_paths)} agent page(s) rendered")
    return 0


if __name__ == "__main__":
    import json
    sys.exit(main())