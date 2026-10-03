"""Public-marking policy for a2a-bridge.

This module resolves *what* is shareable with *whom* under the two-layer
visibility model described in ``ROADMAP.md``:

  Layer 1 (data side)   — frontmatter in the data file:
                          ``<!-- hermes:visibility=public_all -->`` etc.

  Layer 2 (policy side) — the central allowlist at
                          ``~/.hermes/a2a_bridge/public.yaml``

Both layers must agree for a slice to be shareable. This module reads
both, composes them, and exposes:

  * :func:`load_allowlist` — read the central allowlist (or return an
    empty default if it doesn't exist).
  * :func:`scan_frontmatter` — find visibility frontmatter in a memory
    or skill file.
  * :func:`resolve_share` — given a peer and a slice name, return
    whether the slice is shareable with that peer.
  * :func:`list_shareable` — enumerate every slice that the current
    allowlist considers shareable, grouped by level.

The module is **read-only with respect to the data side**: it parses
frontmatter but never writes. The companion write-tool
(``hermes_visibility_set`` in v0.2) will be the only mutator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ``hermes_constants`` is imported lazily inside ``allowlist_path()`` so
# the test venv (which doesn't ship the Hermes agent tree) can still
# import this module.
# from hermes_constants import get_hermes_home


def _get_hermes_home() -> str:
    """Return the HERMES_HOME path, with a graceful fallback.

    Tries ``hermes_constants.get_hermes_home`` first (the real Hermes
    runtime always provides this). Falls back to ``$HERMES_HOME`` or
    ``~/.hermes`` if the constants module isn't on PYTHONPATH.
    """
    try:
        from hermes_constants import get_hermes_home as _real
        return _real()
    except Exception:
        import os
        return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")

# ─────────────────────────────────────────────────────────────────────────
# Visibility levels
# ─────────────────────────────────────────────────────────────────────────


class Level(str, Enum):
    """Three-level visibility model.

    Values are strings so they round-trip through YAML and frontmatter
    cleanly. Order: DENY < PUBLIC_APPROVED < PUBLIC_ALL.
    """

    DENY = "deny"
    PUBLIC_APPROVED = "public_approved"
    PUBLIC_ALL = "public_all"

    def is_shareable(self) -> bool:
        return self != Level.DENY


_LEVEL_FROM_STRING = {l.value: l for l in Level}


def parse_level(value: str) -> Level:
    """Parse a string into a Level; return DENY for unknown / unset."""
    if not value:
        return Level.DENY
    return _LEVEL_FROM_STRING.get(str(value).strip().lower(), Level.DENY)


# ─────────────────────────────────────────────────────────────────────────
# Allowlist (policy side)
# ─────────────────────────────────────────────────────────────────────────

_ALLOWLIST_REL = "a2a_bridge/public.yaml"
_ALLOWLIST_ENV = "HERMES_HOME"


def allowlist_path() -> Path:
    """Resolve the central allowlist path.

    Honors ``HERMES_HOME`` for tests; in production Hermes sets that
    env var to ``~/.hermes``.
    """
    import os

    base = os.environ.get(_ALLOWLIST_ENV)
    if base:
        return Path(base) / _ALLOWLIST_REL
    base = os.environ.get(_ALLOWLIST_ENV) or _get_hermes_home()
    return Path(base) / _ALLOWLIST_REL


@dataclass
class Slice:
    """One shareable slice declared in the central allowlist."""

    name: str
    kind: str                       # "memory" or "skill"
    path: str                       # relative to HERMES_HOME
    level: Level = Level.DENY
    heading_anchor: Optional[str] = None
    approved_peers: List[str] = field(default_factory=list)


@dataclass
class Allowlist:
    """Central allowlist loaded from public.yaml."""

    default_visibility: Level = Level.DENY
    slices: List[Slice] = field(default_factory=list)
    approved_peers: List[str] = field(default_factory=list)
    path: Optional[Path] = None
    diagnostics: List[str] = field(default_factory=list)

    def slice_by_name(self, name: str) -> Optional[Slice]:
        for s in self.slices:
            if s.name == name:
                return s
        return None


def load_allowlist(path: Optional[Path] = None) -> Allowlist:
    """Read the central allowlist. Missing file → empty default."""
    p = path or allowlist_path()
    if not p.exists():
        return Allowlist(path=p)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return Allowlist(
            path=p,
            diagnostics=[f"could not parse {p}: {e}"],
        )

    if not isinstance(raw, dict):
        return Allowlist(
            path=p,
            diagnostics=[f"{p}: top-level must be a YAML mapping"],
        )

    diags: List[str] = []
    default = parse_level(str(raw.get("default_visibility", "") or ""))
    slices: List[Slice] = []
    raw_slices = raw.get("slices", {}) or {}
    if not isinstance(raw_slices, dict):
        diags.append("slices must be a mapping")
    else:
        for kind in ("memories", "skills"):
            entries = raw_slices.get(kind, []) or []
            if not isinstance(entries, list):
                diags.append(f"slices.{kind} must be a list")
                continue
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    diags.append(f"slices.{kind}[{i}] must be a mapping")
                    continue
                name = str(entry.get("name", "")).strip()
                if not name:
                    diags.append(f"slices.{kind}[{i}] missing 'name'")
                    continue
                level = parse_level(str(entry.get("level", "") or ""))
                approved = [str(x).strip() for x in (entry.get("approved_peers") or []) if str(x).strip()]
                slices.append(
                    Slice(
                        name=name,
                        kind="memory" if kind == "memories" else "skill",
                        path=str(entry.get("path", "")).strip(),
                        level=level,
                        heading_anchor=entry.get("heading_anchor"),
                        approved_peers=approved,
                    )
                )

    approved_peers_raw = raw.get("approved_peers", []) or []
    if not isinstance(approved_peers_raw, list):
        diags.append("approved_peers must be a list")
        approved_peers_raw = []
    approved_peers = [str(x).strip() for x in approved_peers_raw if str(x).strip()]

    return Allowlist(
        default_visibility=default,
        slices=slices,
        approved_peers=approved_peers,
        path=p,
        diagnostics=diags,
    )


def save_allowlist(allowlist: Allowlist, path: Optional[Path] = None) -> Path:
    """Write the allowlist back to disk. Returns the path written."""
    p = path or allowlist.path or allowlist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw: Dict[str, Any] = {
        "default_visibility": allowlist.default_visibility.value,
        "slices": {
            "memories": [
                {
                    "name": s.name,
                    "path": s.path,
                    **({"heading_anchor": s.heading_anchor} if s.heading_anchor else {}),
                    "level": s.level.value,
                    **({"approved_peers": s.approved_peers} if s.approved_peers else {}),
                }
                for s in allowlist.slices
                if s.kind == "memory"
            ],
            "skills": [
                {
                    "name": s.name,
                    "path": s.path,
                    "level": s.level.value,
                    **({"approved_peers": s.approved_peers} if s.approved_peers else {}),
                }
                for s in allowlist.slices
                if s.kind == "skill"
            ],
        },
        "approved_peers": allowlist.approved_peers,
    }
    p.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────
# Frontmatter (data side)
# ─────────────────────────────────────────────────────────────────────────

# Two forms we recognize:
#   1. HTML comment in markdown files:
#        <!-- hermes:visibility=public_all audience="*" -->
#   2. YAML frontmatter at the top of any text file (between --- fences):
#        ---
#        name: ...
#        visibility: public_all
#        ---
_HTML_FRONT_MATTER_RE = re.compile(
    r"<!--\s*hermes:visibility\s*=\s*(?P<level>[a-zA-Z_]+)"
    r"(?:\s+[^>]*?)?\s*-->"
)
_YAML_FENCE_RE = re.compile(r"\A\s*---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL)


@dataclass
class Frontmatter:
    """Visibility frontmatter found in a data file."""

    level: Level
    raw: Dict[str, Any] = field(default_factory=dict)
    source: str = ""                # which format was used: "html" or "yaml"

    def is_shareable(self) -> bool:
        return self.level.is_shareable()


def scan_frontmatter(path: Path) -> Optional[Frontmatter]:
    """Look for visibility frontmatter in ``path``.

    Returns ``None`` if the file is unreadable, has no recognized
    frontmatter, or its visibility value is unknown. Otherwise returns
    a :class:`Frontmatter`. The file's content is read at most once.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    # Try YAML frontmatter first (most common for SKILL.md / files
    # that start with ---).
    m = _YAML_FENCE_RE.match(text)
    if m:
        try:
            raw = yaml.safe_load(m.group("body")) or {}
        except yaml.YAMLError:
            raw = {}
        if isinstance(raw, dict):
            value = raw.get("visibility")
            if value is not None:
                return Frontmatter(
                    level=parse_level(str(value)),
                    raw=raw,
                    source="yaml",
                )

    # Fall back to HTML comment (for memory files where adding a YAML
    # fence would be visually awkward).
    m = _HTML_FRONT_MATTER_RE.search(text)
    if m:
        return Frontmatter(
            level=parse_level(m.group("level")),
            raw={"visibility": m.group("level")},
            source="html",
        )
    return None


# ─────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────


def resolve_share(
    allowlist: Allowlist,
    *,
    peer_id: str,
    slice_name: str,
    frontmatter: Optional[Frontmatter] = None,
) -> Tuple[bool, str]:
    """Decide whether ``slice_name`` is shareable with ``peer_id``.

    Returns ``(is_shareable, reason)`` where ``reason`` is a
    human-readable explanation suitable for the user's reply. Both
    layers must agree: frontmatter (data side) and the central
    allowlist (policy side).
    """
    slice_ = allowlist.slice_by_name(slice_name)
    if slice_ is None:
        return False, f"slice '{slice_name}' is not in the central allowlist"

    if not slice_.level.is_shareable():
        return False, (
            f"slice '{slice_name}' has level={slice_.level.value} in the "
            f"central allowlist; not shareable"
        )

    if slice_.level == Level.PUBLIC_ALL:
        if peer_id not in allowlist.approved_peers and allowlist.approved_peers:
            # approved_peers non-empty means "only these peers" even
            # for public_all. Empty list = open to any peer on the
            # met list (caller's job to gate on the meeting).
            return False, (
                f"slice '{slice_name}' is public_all but the central "
                f"allowlist's approved_peers is non-empty and does not "
                f"include {peer_id}"
            )
    elif slice_.level == Level.PUBLIC_APPROVED:
        if peer_id not in slice_.approved_peers and peer_id not in allowlist.approved_peers:
            return False, (
                f"slice '{slice_name}' is public_approved; {peer_id} is "
                f"not in the slice's approved_peers or the global "
                f"approved_peers list"
            )

    # Frontmatter layer (data side). If present, it must agree with
    # the policy layer. If absent, the policy layer wins.
    if frontmatter is not None:
        if not frontmatter.level.is_shareable():
            return False, (
                f"data-side frontmatter for slice '{slice_name}' declares "
                f"visibility={frontmatter.level.value}; refusing to "
                f"override the central allowlist"
            )
        # If both layers are shareable, require they agree on the level
        # (public_all is the loosest, so it widens policy-side decisions
        # but doesn't tighten them).
        if frontmatter.level == Level.DENY:
            return False, "data-side frontmatter explicitly denies sharing"

    return True, (
        f"slice '{slice_name}' is shareable with {peer_id} at level "
        f"{slice_.level.value}"
    )


def list_shareable(
    allowlist: Allowlist,
    *,
    peer_id: str,
    memories: List[Path],
    skills: List[Path],
) -> List[Tuple[Slice, Optional[Frontmatter], bool, str]]:
    """Enumerate every slice and report whether it's shareable with ``peer_id``.

    Returns a list of (slice, frontmatter_or_none, shareable_bool, reason)
    tuples in allowlist order. Useful for the dry-run helper.
    """
    out: List[Tuple[Slice, Optional[Frontmatter], bool, str]] = []
    for s in allowlist.slices:
        if s.path:
            candidate = Path(s.path)
            if not candidate.is_absolute():
                import os
                base = os.environ.get(_ALLOWLIST_ENV) or _get_hermes_home()
                candidate = Path(base) / s.path
            fm = scan_frontmatter(candidate) if candidate.exists() else None
        else:
            fm = None
        shareable, reason = resolve_share(allowlist, peer_id=peer_id, slice_name=s.name, frontmatter=fm)
        out.append((s, fm, shareable, reason))
    return out
