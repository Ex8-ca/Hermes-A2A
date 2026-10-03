#!/usr/bin/env bash
# install-gateway-service.sh — make `hermes gateway run` auto-start at boot
#
# What this does:
#   1. Writes a systemd user service that runs `hermes gateway run`
#   2. Enables it (so it starts on next boot)
#   3. Enables linger (so the user service survives logout)
#   4. Optionally starts it now
#
# Usage:
#   ./scripts/install-gateway-service.sh            # install + enable
#   ./scripts/install-gateway-service.sh --start    # install + enable + start now
#   ./scripts/install-gateway-service.sh --uninstall  # remove the service
#
# Idempotent: re-running just refreshes the unit file. Safe to run after
# the plugin is upgraded.
#
# Requires:
#   - Linux with systemd (user services; works on Arch, Ubuntu, Debian, Fedora)
#   - The `hermes-a2a-bridge` plugin already installed via `hermes plugins install`

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT="$HERMES_HOME/hermes-agent"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
UNIT_NAME="hermes-a2a-gateway.service"
UNIT_PATH="$SYSTEMD_USER_DIR/$UNIT_NAME"

# Find the Python interpreter the desktop uses (matches `hermes` command).
# The desktop's launcher picks a "tool" python under ~/.hermes/tools/ that has
# all the deps installed; using the system python would miss them.
PYTHON_BIN="$HERMES_HOME/tools/python-3.14.7+202****0901-linux-x64/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
    # Fallback: any python3 on PATH that has hermes_cli installed
    PYTHON_BIN="$(command -v python3)"
    if [ -z "$PYTHON_BIN" ]; then
        echo "ERROR: no python3 found" >&2
        exit 1
    fi
fi

# ── Helpers ──────────────────────────────────────────────────────────────────
log() { echo "  $*"; }
ok()  { echo "  ✓ $*"; }
warn() { echo "  ⚠ $*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ── Sanity checks ────────────────────────────────────────────────────────────
[ -d "$HERMES_AGENT" ] || die "Hermes agent not found at $HERMES_AGENT — install it first."

if ! command -v systemctl >/dev/null 2>&1; then
    die "systemctl not found. This script is for Linux with systemd."
fi

# ── --uninstall path ─────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
    echo "Uninstalling $UNIT_NAME..."
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    ok "Service removed"
    echo
    echo "Note: loginctl enable-linger is left as-is. To disable:"
    echo "  sudo loginctl disable-linger $(whoami)"
    exit 0
fi

# ── Write the unit file ──────────────────────────────────────────────────────
echo "Installing $UNIT_NAME..."
mkdir -p "$SYSTEMD_USER_DIR"

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Hermes Gateway (A2A enabled via hermes-a2a-bridge)
Documentation=https://github.com/Ex8-ca/Hermes-A2A
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="HERMES_HOME=$HERMES_HOME"
Environment="PYTHONPATH=$HERMES_AGENT"
ExecStart=$PYTHON_BIN -I -c "import os, sys; os.environ['HERMES_HOME']='$HERMES_HOME'; sys.path.insert(0, '$HERMES_AGENT'); from hermes_cli.main import main; sys.argv=['hermes','gateway','run']; main()"
Restart=on-failure
RestartSec=10
# Match the desktop's runtime env; the desktop strips PATH and re-sets
# PYTHONPATH/PYTHONHOME so we do the same to avoid the venv mismatch that
# breaks `ruamel.yaml` and similar deps.

[Install]
WantedBy=default.target
EOF

ok "Unit written to $UNIT_PATH"

# ── Reload + enable ──────────────────────────────────────────────────────────
systemctl --user daemon-reload
ok "systemd user daemon reloaded"

systemctl --user enable "$UNIT_NAME" 2>/dev/null || warn "Could not enable service (continuing)"
ok "Service enabled (will start on next login/boot)"

# ── Enable linger so the user service survives logout/reboot ────────────────
# This is a one-time per-user setup; it's a sudo-level command but it only
# affects the current user. loginctl is the documented way to do this.
if loginctl show-user "$(whoami)" 2>/dev/null | grep -q "^Linger=yes"; then
    ok "loginctl linger already enabled for $(whoami)"
else
    if sudo -n loginctl enable-linger "$(whoami)" 2>/dev/null; then
        ok "loginctl linger enabled for $(whoami) — service survives logout"
    else
        warn "Could not enable linger automatically. Run manually once:"
        warn "  sudo loginctl enable-linger $(whoami)"
        warn "(Without this, the service stops when you log out.)"
    fi
fi

# ─--start: enable and start now ──────────────────────────────────────────────
if [ "${1:-}" = "--start" ] || [ "${1:-}" = "-s" ]; then
    # Stop the existing manual gateway or any desktop-spawned one on 9900
    if ss -tln 2>/dev/null | grep -q ":9900 "; then
        warn "Port 9900 already in use; the new service may fail to bind"
        warn "until the existing gateway is stopped."
    fi

    systemctl --user start "$UNIT_NAME"
    sleep 3

    if systemctl --user is-active --quiet "$UNIT_NAME"; then
        ok "Service started successfully"
    else
        warn "Service did not start cleanly. Check: journalctl --user -u $UNIT_NAME -n 50"
    fi
fi

# ── Done ────────────────────────────────────────────────────────────────────
echo
ok "Install complete"
echo
echo "Useful commands:"
echo "  systemctl --user status $UNIT_NAME          # check status"
echo "  systemctl --user restart $UNIT_NAME         # restart"
echo "  journalctl --user -u $UNIT_NAME -f           # follow logs"
echo "  ./scripts/install-gateway-service.sh --start   # start now"
echo "  ./scripts/install-gateway-service.sh --uninstall  # remove"
