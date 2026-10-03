"""Test configuration.

Adds the parent directory of ``plugins/a2a_bridge/`` to sys.path so the
test files (which import as ``from plugins.a2a_bridge import ...``) can
resolve their sibling modules without needing a full Hermes agent tree.

In production, Hermes adds the right paths at startup, so this is a test
convenience only.

We *prepend* (and also remove any pre-existing /home/marc/.hermes/hermes-agent
or similar live-install path that would shadow our test copy) so that the
version of the plugin in this repo wins the import resolution order.
"""

from __future__ import annotations

import sys
from pathlib import Path

# plugins/a2a_bridge/tests/conftest.py -> plugins/a2a_bridge -> plugins
PLUGINS_PARENT = Path(__file__).resolve().parent.parent.parent

# Drop any pre-existing live-install paths that would shadow our copy.
# These are the paths that the Hermes agent's runtime adds at boot.
_LIVE_INSTALL_PATTERNS = (
    "/home/marc/.hermes/hermes-agent",
    "/home/marc/.hermes/hermes-agent/plugins",
)
sys.path[:] = [p for p in sys.path if not any(p.startswith(pat) for pat in _LIVE_INSTALL_PATTERNS)]

# And put ours at the front.
plugins_parent_str = str(PLUGINS_PARENT)
if plugins_parent_str in sys.path:
    sys.path.remove(plugins_parent_str)
sys.path.insert(0, plugins_parent_str)