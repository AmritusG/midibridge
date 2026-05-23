#!/usr/bin/env bash
# Install midibridge as a systemd service.
#
# Run this script from inside the midibridge checkout, as your normal user
# (it will sudo for the parts that need root). The script generates a
# systemd unit file from a template, substituting the current user and
# the absolute path to the checkout, so it works wherever you cloned to.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DST="/etc/systemd/system/midibridge.service"

# Detect the user who should run the bridge. If invoked with sudo, prefer
# the original user; otherwise use whoever's running the script.
RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"

if [ ! -f "${SCRIPT_DIR}/bridge.py" ]; then
  echo "bridge.py not found in ${SCRIPT_DIR}. Run this from the midibridge checkout." >&2
  exit 1
fi

if [ ! -f "${SCRIPT_DIR}/.venv/bin/python3" ]; then
  echo "Virtualenv not found at ${SCRIPT_DIR}/.venv" >&2
  echo "Create it first with:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -f "${SCRIPT_DIR}/config.yaml" ]; then
  echo "config.yaml not found. Copy and edit the example:" >&2
  echo "  cp config.example.yaml config.yaml" >&2
  echo "  nano config.yaml" >&2
  exit 1
fi

# Generate the unit file
TMP_UNIT="$(mktemp)"
cat > "${TMP_UNIT}" <<UNIT
[Unit]
Description=midibridge — X-Touch Extender to XR18 OSC bridge
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/bridge.py
Restart=always
RestartSec=3

# Stdout/stderr go to journalctl
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

echo "Installing service unit to ${SERVICE_DST}"
echo "  User:    ${RUN_USER}"
echo "  Path:    ${SCRIPT_DIR}"
echo

sudo cp "${TMP_UNIT}" "${SERVICE_DST}"
sudo chmod 644 "${SERVICE_DST}"
rm "${TMP_UNIT}"

sudo systemctl daemon-reload
sudo systemctl enable midibridge.service
sudo systemctl restart midibridge.service

echo
echo "midibridge installed and started."
echo
echo "Useful commands:"
echo "  systemctl status midibridge       # check state"
echo "  journalctl -u midibridge -f       # tail logs"
echo "  systemctl restart midibridge      # restart"
echo "  systemctl stop midibridge         # stop"
echo "  systemctl disable midibridge      # don't start on boot"
echo
echo "For setup mode (knobs live, bus mode usable), use the included wrapper:"
echo "  ./unlock-trim.sh"
echo
echo "Or manually:"
echo "  sudo systemctl stop midibridge"
echo "  .venv/bin/python3 bridge.py --setup"
echo
