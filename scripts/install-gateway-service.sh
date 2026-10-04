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
# Forks `hermes gateway run` into the background, writes the PID to
# ~/.hermes/a2a-bridge/gateway.pid, and exits 0 immediately. This
# matches the Type=oneshot + RemainAfterExit=yes contract: the
# service is "active" as soon as the wrapper returns, even though
# the gateway keeps running.
#
# If a gateway is already running (per-profile singleton, exit 75
# from the inner command), we map that to 0 too — the service is
# "active" because a gateway is up, just not via us.
set -e
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export PYTHONPATH="$HERMES_HOME/hermes-agent"
PYTHON_BIN="$(ls -td "$HERMES_HOME"/tools/python-*/bin/python3 2>/dev/null | head -1)"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: no hermes-managed python3 found under $HERMES_HOME/tools/" >&2
    exit 1
fi

PIDFILE="$HERMES_HOME/a2a-bridge/gateway.pid"
LOGFILE="$HERMES_HOME/a2a-bridge/gateway.log"
mkdir -p "$(dirname "$PIDFILE")"

# If a previous PID file exists and the process is still alive, do
# nothing — systemd's job is "the gateway is up", not "we started
# it just now". This is what makes the service safe to call from
# login scripts too.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Gateway already running (pid $(cat "$PIDFILE")) — leaving it alone."
    exit 0
fi

# Launch the gateway in a detached process group, write the PID.
# `setsid` so the new process is its own session leader (survives
# the wrapper's exit; not affected by Ctrl-C on the calling tty).
# `</dev/null` so the gateway doesn't keep the wrapper's stdin.
setsid "$PYTHON_BIN" -I -c "
import os, sys
os.environ['HERMES_HOME'] = os.environ['HERMES_HOME']
sys.path.insert(0, os.environ['PYTHONPATH'])
from hermes_cli.main import main
sys.argv = ['hermes', 'gateway', 'run']
main()
" </dev/null >"$LOGFILE" 2>&1 &
GW_PID=$!
echo "$GW_PID" > "$PIDFILE"
echo "Gateway launched (pid $GW_PID); see $LOGFILE."
# Give the gateway a moment to either succeed or fail loudly.
# hermes gateway run is a per-profile/host singleton: if another
# gateway is already listening on 9900, this one exits almost
# immediately. Detect that by checking the log for the singleton
# message; if found, find the *existing* gateway and point the
# PID file at it. Either way, systemd's job is "a gateway is up
# on 9900", not "we started it just now".
sleep 2
if ! kill -0 "$GW_PID" 2>/dev/null; then
    if grep -q "already serves" "$LOGFILE" 2>/dev/null; then
        # Another gateway is already up. Find it via ss on port 9900
        # and write *its* PID to the PID file.
        EXISTING=$(ss -tlnpH 'sport = :9900' 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
        if [ -n "$EXISTING" ] && kill -0 "$EXISTING" 2>/dev/null; then
            echo "$EXISTING" > "$PIDFILE"
            echo "Existing gateway (pid $EXISTING) is serving 9900; tracking it instead."
            exit 0
        fi
        echo "Another gateway is on 9900 but I can't identify its PID. Leaving no PID file." >&2
        rm -f "$PIDFILE"
        exit 0
    fi
    echo "Gateway failed to start; see $LOGFILE." >&2
    rm -f "$PIDFILE"
    exit 1
fi
exit 0
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
#   - systemd runs the wrapper at boot (and on first \`systemctl start\`)
#   - the wrapper forks the gateway into a new session and exits 0
#     immediately, so the service is "active" as soon as the wrapper
#     returns. The gateway keeps running independently.
#   - the wrapper writes the gateway's PID to
#     $HERMES_HOME/a2a-bridge/gateway.pid, which ExecStop uses to
#     terminate cleanly on \`systemctl stop\`.
#   - we do NOT auto-restart on exit; the gateway is supposed to run
#     until you stop it (systemctl --user stop). If it dies, the next
#     \`systemctl start\` will replace it.
TimeoutStartSec=15
ExecStop=/bin/bash -c 'if [ -f "$HERMES_HOME/a2a-bridge/gateway.pid" ]; then kill -TERM "\$(cat "$HERMES_HOME/a2a-bridge/gateway.pid")" 2>/dev/null || true; rm -f "$HERMES_HOME/a2a-bridge/gateway.pid"; fi'

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
