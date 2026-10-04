#!/usr/bin/env python3
"""Operator-rotation CLI for the Hermes-A2A directory.

The directory's `submit.js` reads a `ROOT_SYSTEM_POLICY` Cloudflare
Pages env var (JSON-string with a list of approvers). This CLI
manages that policy file safely:

  * ``--list``         Print the current approvers
  * ``--add NAME KEY`` Add a new approver (32-byte ed25519 pubkey, base64)
  * ``--remove NAME``  Remove an approver (refuse if it would empty the list)

The CLI writes the policy to a local file (default
``~/.hermes/directory.policy.json``, mode 0600) and appends each
change to an audit log (default
``~/.hermes/directory.policy.audit.log``, mode 0600). The operator
copies the resulting policy into the Cloudflare dashboard's
`ROOT_SYSTEM_POLICY` env var and redeploys.

The CLI does NOT make Cloudflare API calls — that requires an API
token, which we don't bake into this repo. Operators can wire
``wrangler pages secret put ROOT_SYSTEM_POLICY`` into their
deploy script if they want the policy file → Pages env var to be
automated; the v0.3 CLI just produces the JSON.

The fail-closed invariant: a policy with an empty approvers list
is refused. submit.js would refuse every submission anyway, but
the CLI refuses to *create* that state in the first place.

Usage:
    python3 -m directory.operator.policy_rotate --list
    python3 -m directory.operator.policy_rotate --add alice ed25519:AAAA...
    python3 -m directory.operator.policy_rotate --remove alice

Exit codes:
    0 — success
    1 — user error (bad args, missing name/key, etc.)
    2 — invariant violation (would empty approvers, duplicate name, etc.)
    3 — file I/O error
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Defaults match the `hermes` config layout the operator already uses.
DEFAULT_POLICY_PATH = Path.home() / ".hermes" / "directory.policy.json"
DEFAULT_AUDIT_PATH = Path.home() / ".hermes" / "directory.policy.audit.log"

POLICY_VERSION = 1


class PolicyError(Exception):
    """A user/invariant error in policy rotation. Exit code 2."""


def read_policy(path: Path) -> Dict[str, Any]:
    """Read the policy file. Return a fresh empty policy if missing."""
    if not path.exists():
        return {"approvers": [], "version": POLICY_VERSION}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PolicyError(f"failed to read policy file: {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PolicyError(f"policy file is not valid JSON: {e}")
    if not isinstance(data, dict) or "approvers" not in data:
        raise PolicyError(f"policy file is missing 'approvers' key: {path}")
    if not isinstance(data["approvers"], list):
        raise PolicyError(f"policy 'approvers' is not a list: {path}")
    return data


def write_policy(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write the policy file with mode 0600.

    We write to a temp file in the same directory and ``os.replace``
    to avoid leaving a half-written policy on disk if the process is
    killed mid-write. Permissions are set before the rename so the
    temp file is never world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _append_audit(audit_path: Path, entry: Dict[str, Any]) -> None:
    """Append a JSONL line to the audit log. Creates the file at
    mode 0600 if it doesn't exist."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    if not audit_path.exists():
        # First write: create with mode 0600 from the start
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
    line = json.dumps(entry, sort_keys=True)
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def list_approvers(policy_path: Path) -> List[Dict[str, Any]]:
    """Return the current list of approvers, in insertion order."""
    return list(read_policy(policy_path).get("approvers", []))


def add_approver(
    policy_path: Path,
    audit_path: Path,
    *,
    name: str,
    pubkey_b64: str,
) -> None:
    """Add an approver. Raises PolicyError on duplicate name."""
    if not name or not name.strip():
        raise PolicyError("approver name must be non-empty")
    if not pubkey_b64 or not pubkey_b64.strip():
        raise PolicyError("approver pubkey must be non-empty")
    name = name.strip()
    pubkey_b64 = pubkey_b64.strip()
    policy = read_policy(policy_path)
    if any(a.get("name") == name for a in policy["approvers"]):
        raise PolicyError(f"approver {name!r} already exists")
    policy["approvers"].append({
        "name": name,
        "ed25519_pubkey_b64": pubkey_b64,
        "added_at": _now_iso(),
    })
    write_policy(policy_path, policy)
    _append_audit(audit_path, {
        "ts": _now_iso(),
        "action": "add",
        "name": name,
        "pubkey_b64_prefix": pubkey_b64[:24],
    })


def remove_approver(
    policy_path: Path,
    audit_path: Path,
    *,
    name: str,
) -> None:
    """Remove an approver. Refuse if it would empty the list."""
    name = name.strip()
    policy = read_policy(policy_path)
    if not any(a.get("name") == name for a in policy["approvers"]):
        raise PolicyError(f"approver {name!r} not found")
    if len(policy["approvers"]) <= 1:
        raise PolicyError(
            "refusing to remove the last approver: the directory would "
            "be locked out (submit.js fails-closed on empty approvers). "
            "Add a replacement first, then remove the old one."
        )
    policy["approvers"] = [
        a for a in policy["approvers"] if a.get("name") != name
    ]
    write_policy(policy_path, policy)
    _append_audit(audit_path, {
        "ts": _now_iso(),
        "action": "remove",
        "name": name,
    })


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manage the Hermes-A2A directory's operator allowlist.",
    )
    p.add_argument(
        "--policy-file", type=Path, default=DEFAULT_POLICY_PATH,
        help=f"Path to the policy JSON (default: {DEFAULT_POLICY_PATH})",
    )
    p.add_argument(
        "--audit-file", type=Path, default=DEFAULT_AUDIT_PATH,
        help=f"Path to the audit log (default: {DEFAULT_AUDIT_PATH})",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--list", action="store_true",
        help="List current approvers and exit.",
    )
    g.add_argument(
        "--add", nargs=2, metavar=("NAME", "PUBKEY_B64"),
        help="Add a new approver. PUBKEY_B64 is the 32-byte ed25519 "
             "public key, base64-encoded (with or without the "
             "'ed25519:' prefix).",
    )
    g.add_argument(
        "--remove", metavar="NAME",
        help="Remove an approver by name. Refuses if it would empty the list.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)
    try:
        if args.list:
            for a in list_approvers(args.policy_file):
                print(f"{a['name']:20s}  {a.get('ed25519_pubkey_b64', '?')[:24]}…  added={a.get('added_at', '?')}")
            return 0
        if args.add:
            name, pubkey = args.add
            add_approver(
                args.policy_file, args.audit_file,
                name=name, pubkey_b64=pubkey,
            )
            print(f"added approver {name!r}")
            return 0
        if args.remove:
            remove_approver(
                args.policy_file, args.audit_file,
                name=args.remove,
            )
            print(f"removed approver {args.remove!r}")
            return 0
    except PolicyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"i/o error: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
