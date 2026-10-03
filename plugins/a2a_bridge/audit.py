"""Audit-log reader for a2a-bridge.

Reads ~/.hermes/a2a_audit.jsonl and surfaces the most recent entries for
a given peer (or all peers). Used by the a2a_bridge_audit tool.

The plugin doesn't write to this log — the underlying A2A platform
plugin does, on every inbound and outbound task. We just read.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_AUDIT_REL = "a2a_audit.jsonl"
_AUDIT_ENV = "HERMES_HOME"           # override for tests
_MAX_ROWS = 200                      # don't pull the entire log into context


def _audit_path() -> Path:
    """Resolve ~/.hermes/a2a_audit.jsonl lazily.

    Honors HERMES_HOME for tests and CI; in production Hermes always
    sets that env var to the user's real ~/.hermes directory. We avoid
    importing ``hermes_constants`` so the plugin's tests can run without
    the full Hermes agent source tree on PYTHONPATH.
    """
    base = os.environ.get(_AUDIT_ENV)
    if base:
        return Path(base) / _AUDIT_REL
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home()) / _AUDIT_REL
    except Exception:
        # Last-resort default; tests that need real audit data set
        # HERMES_HOME explicitly.
        return Path.home() / ".hermes" / _AUDIT_REL


def read_audit(
    peer: Optional[str] = None,
    direction: Optional[str] = None,
    last: int = 20,
) -> List[Dict[str, Any]]:
    """Return the most recent audit entries, newest last.

    Filters:
      peer       — substring match against the 'peer' field
                   (e.g. "ip:127.0.0.1", "desktop", "ai386.3").
      direction  — "inbound" or "outbound". None = both.
      last       — how many to return after filtering (capped at _MAX_ROWS).
    """
    p = _audit_path()
    if not p.exists():
        return []

    rows: List[Dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            # Audit lines are appended-only; for "last N" we'd normally
            # seek. For modest files just read all.
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if peer and peer not in str(row.get("peer", "")):
                    continue
                if direction and row.get("direction") != direction:
                    continue
                rows.append(row)
    except OSError:
        return []

    # Cap at last N (after filter, so we get the truly newest ones).
    if not rows:
        return []
    # Use slice notation, NOT negative-index lookup: ``lst[-N]`` raises
    # IndexError when N > len(lst) (a long-standing CPython behavior
    # that some readers wrongly assume clamps). Slicing returns a
    # shorter list, which is what we want here.
    return rows[-max(1, min(last, _MAX_ROWS)):]


def format_audit_table(rows: List[Dict[str, Any]]) -> str:
    """Render a small markdown table the agent can paste into a reply."""
    if not rows:
        return "(no matching audit entries)"

    lines = [
        f"| {'time':<19} | {'dir':<5} | {'peer':<18} | {'task_id':<26} | summary",
        f"|{'-'*21}|{'-'*7}|{'-'*20}|{'-'*28}|--------",
    ]
    for r in rows:
        ts = r.get("ts")
        try:
            when = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            when = "?"
        peer = (r.get("peer") or "?")[:18]
        tid = (r.get("task_id") or "?")[:26]
        direction = r.get("direction") or "?"
        summary = (r.get("summary") or "").replace("|", "\\|")
        if len(summary) > 80:
            summary = summary[:79] + "…"
        lines.append(f"| {when:<19} | {direction:<5} | {peer:<18} | {tid:<26} | {summary}")
    return "\n".join(lines)


def list_peers() -> List[Dict[str, Any]]:
    """Return one summary row per distinct peer seen in the audit log.

    Useful when the agent wants to know which peers it's talked to
    recently without going through the a2a plugin's config view.
    """
    p = _audit_path()
    if not p.exists():
        return []
    by_peer: Dict[str, Dict[str, Any]] = {}
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                peer = str(row.get("peer") or "?")
                if peer not in by_peer:
                    by_peer[peer] = {"peer": peer, "count": 0, "last_ts": None}
                cur = by_peer[peer]
                cur["count"] += 1
                ts_raw = row.get("ts")
                if ts_raw is None:
                    continue
                try:
                    ts = float(ts_raw)
                except (TypeError, ValueError):
                    continue
                if cur["last_ts"] is None or ts > cur["last_ts"]:
                    cur["last_ts"] = ts
    except OSError:
        return []

    out = []
    for v in by_peer.values():
        last_ts = v.get("last_ts")
        if last_ts is None:
            v["last_seen"] = "?"
        else:
            try:
                v["last_seen"] = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError):
                v["last_seen"] = "?"
        v.pop("last_ts", None)
        out.append(v)
    out.sort(key=lambda x: (x.get("last_seen") == "?", x.get("last_seen") or ""), reverse=True)
    return out


def format_peer_list(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "(no peers seen yet — make a call first)"
    lines = [
        f"| {'peer':<20} | {'count':>5} | {'last_seen':<19}",
        f"|{'-'*22}|{'-'*7}|{'-'*21}",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('peer', '?'):<20} | {r.get('count', 0):>5} | {r.get('last_seen', '?'):<19}"
        )
    return "\n".join(lines)