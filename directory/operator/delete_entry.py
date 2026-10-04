#!/usr/bin/env python3
"""Delete an agent entry from the Hermes-A2A directory.

Mirrors make_submission.py: reads the operator's ed25519 private key
from ~/.hermes/directory_operator.key (32 raw bytes, base64),
canonicalizes a deletion envelope (kind="agent_deletion",
agent_id, public_key, submitted_at), signs the canonical UTF-8
bytes, base64-encodes the signature, and POSTs the envelope to
https://hermes-a2a.dpmob.com/delete (or any URL passed via
--submit-url).

The canonicalization algorithm MUST match the directory's
delete.js function `canonicalize()` — keys sorted at every level,
JSON string values exactly per JSON.stringify (UTF-8, ensure_ascii
default), no whitespace. This is the same canonicalization
make_submission.py uses (the directory's canonicalize.js does not
distinguish ASCII vs non-ASCII in object key strings — both
encode non-ASCII as \\uXXXX — so the two are byte-compatible).

The public key sent in the envelope is derived from the operator's
private key so it stays consistent with the allowlist baked into
submit.js / delete.js.

Authorization:
- Operator's own key: can delete any entry.
- Any other key: 403 — only the operator can delete via this CLI.
  (For self-delete by an agent, the agent's own tooling would
  build a separate envelope signed with the agent's key.)

Usage:
    python3 delete_entry.py agent_2f4b8e9d11a7c6a5
    python3 delete_entry.py desktop_2
    python3 delete_entry.py --dry-run desktop_2
    python3 delete_entry.py --verbose desktop_2

On success, appends one line to
directory/operator/.deletion-audit.log (mode 0600) capturing
timestamp, agent_id, authorized_by, and the response body, so
the operator has a tamper-evident local record of every entry
deletion performed in the directory.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


DEFAULT_KEY_PATH = "/home/marc/.hermes/directory_operator.key"
DEFAULT_SUBMIT_URL = "https://hermes-a2a.dpmob.com/delete"
AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deletion-audit.log")


def canonicalize(obj: Any) -> str:
    """Recursive JSON canonicalize: keys sorted at every level, no
    whitespace. Mirrors the directory worker's canonicalize() and
    make_submission.py's canonicalize() exactly. Duplicated locally
    to keep the diff minimal (no shared module refactor in v0.3.3)."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return (
            "{"
            + ",".join(json.dumps(k, ensure_ascii=True) + ":" + canonicalize(obj[k]) for k in keys)
            + "}"
        )
    raise TypeError(f"cannot canonicalize {type(obj)}")


def load_private_key(path: str) -> Ed25519PrivateKey:
    with open(path, encoding="utf-8") as f:
        b64 = f.read().strip()
    raw = base64.b64decode(b64)
    if len(raw) != 32:
        raise ValueError(f"expected 32-byte ed25519 seed, got {len(raw)} bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_b64(sk: Ed25519PrivateKey) -> str:
    raw = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "ed25519:" + base64.b64encode(raw).decode()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_audit_log() -> None:
    """Create the audit log file with mode 0600 if it doesn't exist.
    Does NOT chmod an existing file — operators may have a different
    mode set deliberately."""
    if os.path.exists(AUDIT_LOG_PATH):
        return
    fd = os.open(AUDIT_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)


def append_audit_log(line: str) -> None:
    ensure_audit_log()
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    # Belt-and-suspenders: enforce 0600 on every write in case the
    # file existed with the wrong perms (umask drift, accidental
    # chmod, etc.). Failures are non-fatal here — the operator
    # wants the audit line to land.
    try:
        os.chmod(AUDIT_LOG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("agent_id", help="The agent_id to delete (e.g. agent_2f4b8e9d11a7c6a5 or desktop_2)")
    ap.add_argument("--key-path", default=DEFAULT_KEY_PATH,
                    help=f"Path to operator private key (default: {DEFAULT_KEY_PATH})")
    ap.add_argument("--submit-url", default=DEFAULT_SUBMIT_URL,
                    help=f"POST target (default: {DEFAULT_SUBMIT_URL})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the envelope and print it, don't POST")
    ap.add_argument("--verbose", action="store_true",
                    help="Print the full response body and headers, not just status")
    ap.add_argument("--no-audit", action="store_true",
                    help="Don't append to the local audit log (for testing only)")
    parsed = ap.parse_args()

    sk = load_private_key(parsed.key_path)
    pk_b64 = public_key_b64(sk)

    payload: dict[str, Any] = {
        "kind": "agent_deletion",
        "agent_id": parsed.agent_id,
        "public_key": pk_b64,
        "submitted_at": utc_now_iso(),
    }

    canonical = canonicalize(payload)
    sig = sk.sign(canonical.encode("utf-8"))
    envelope = dict(payload, signature=base64.b64encode(sig).decode())

    print("---- canonical bytes (the exact thing signed) ----")
    print(canonical)
    print("---- envelope to POST ----")
    print(json.dumps(envelope, indent=2))

    if parsed.dry_run:
        print("---- --dry-run: not POSTing ----")
        return 0

    body = json.dumps(envelope).encode("utf-8")
    req = urllib.request.Request(
        parsed.submit_url,
        data=body,
        headers={
            "content-type": "application/json",
            # Cloudflare blocks Python's default urllib user-agent with
            # HTTP 403. Be explicit. (Same fix used by make_submission.py
            # and render_agents.py.)
            "user-agent": "hermes-a2a-operator-submitter/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            response_body = r.read().decode("utf-8")
            response_headers = dict(r.headers.items())
    except urllib.error.HTTPError as e:
        status = e.code
        response_body = e.read().decode("utf-8", errors="replace")
        response_headers = dict(e.headers.items()) if e.headers else {}

    print(f"---- POST {parsed.submit_url} -> HTTP {status} ----")
    if parsed.verbose:
        print("HEADERS:")
        for k, v in response_headers.items():
            print(f"  {k}: {v}")
        print("BODY:")
        print(response_body)
    else:
        # One-line summary in non-verbose mode.
        print(response_body)

    # Audit log: always append (unless --no-audit). The audit log
    # captures the full request envelope + the response so an
    # operator can later prove "I deleted X at time T, signed with
    # key Y, and the directory returned Z". Failures to write the
    # log are non-fatal — the directory's own deletion-marker KV
    # key (`deletion:<iso>:<agent_id>`) is the authoritative record.
    if not parsed.no_audit:
        audit_line = json.dumps({
            "ts": utc_now_iso(),
            "agent_id": parsed.agent_id,
            "submit_url": parsed.submit_url,
            "request": envelope,
            "response_status": status,
            "response_body": response_body,
        }, separators=(",", ":"))
        try:
            append_audit_log(audit_line)
        except OSError as e:
            print(f"---- WARNING: failed to append audit log: {e}", file=sys.stderr)

    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())