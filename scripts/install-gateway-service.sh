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
WRAPPER_PATH="$HOME/.local/bin/run-hermes-gateway.sh"

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

# ── Write the wrapper script ─────────────────────────────────────────────────
# The wrapper handles picking the right Python interpreter under
# ~/.hermes/tools/ — that directory name has special characters on some
# installs (a literal `+` and `****`) which systemd refuses in ExecStart,
# so we use a clean-path script instead of hardcoding the path.
mkdir -p "$(dirname "$WRAPPER_PATH")"
cat > "$WRAPPER_PATH" <<'WRAPPER_EOF'
#!/usr/bin/env bash
# run-hermes-gateway.sh — wrapper for the systemd service
# Wraps `hermes gateway run` with the right Python interpreter and env.
set -e
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export PYTHONPATH="$HERMES_HOME/hermes-agent"
PYTHON_BIN="$(ls -td "$HERMES_HOME"/tools/python-*/bin/python3 2>/dev/null | head -1)"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: no hermes-managed python3 found under $HERMES_HOME/tools/" >&2
    exit 1
fi
# Run the gateway. If another gateway is already running, the per-profile
# singleton gate inside `hermes gateway run` exits with status 75. That's
# NOT a failure from systemd's point of view (the gateway is up, just not
# via us), so map it to 0. Anything else passes through.
set +e
"$PYTHON_BIN" -I -c "
import os, sys
os.environ['HERMES_HOME'] = os.environ['HERMES_HOME']
sys.path.insert(0, os.environ['PYTHONPATH'])
from hermes_cli.main import main
sys.argv = ['hermes', 'gateway', 'run']
main()
" < /dev/null
rc=$?
set -e
if [ "$rc" -eq 75 ]; then
    echo "Another gateway already serves default — leaving it running."
    exit 0
fi
exit "$rc"
WRAPPER_EOF
chmod +x "$WRAPPER_PATH"
ok "Wrapper written to $WRAPPER_PATH"

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
Type=oneshot
RemainAfterExit=yes
Environment="HERMES_HOME=$HERMES_HOME"
Environment="PYTHONPATH=$HERMES_AGENT"
ExecStart=$WRAPPER_PATH
# Match the desktop's runtime env; the desktop strips PATH and re-sets
# PYTHONPATH/PYTHONHOME so we do the same to avoid the venv mismatch that
# breaks \`ruamel.yaml\` and similar deps.
#
# Type=oneshot + RemainAfterExit=yes means:
#   - systemd runs the command at boot (and on first \`systemctl start\`)
#   - the wrapper script maps the gateway's "another instance is running"
#     exit code (75) to 0, so the service succeeds whether we start the
#     gateway ourselves or find one already running (per-profile singleton)
#   - we do NOT auto-restart on exit; the gateway is supposed to run until
#     you stop it (systemctl --user stop). If it dies, the next service
#     start (or a manual systemctl start) will replace it.
TimeoutStartSec=45

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
