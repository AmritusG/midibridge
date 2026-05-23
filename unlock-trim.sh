#!/bin/bash
# Temporarily unlock trim knobs for sound-check adjustments.
#
# Stops the locked systemd service, runs the bridge interactively in
# --setup mode (trim knobs live), and on Ctrl+C automatically restarts
# the locked service.
#
# Run on the Pi (or via ssh).

set -u
cd "$(dirname "$0")"

echo "============================================================"
echo "  TRIM UNLOCK SESSION"
echo "  Trim knobs are live. Adjust as needed."
echo "  Press Ctrl+C when done to relock and restart the service."
echo "============================================================"
echo

cleanup() {
    echo
    echo "Restarting locked service..."
    sudo systemctl start midibridge
    echo "Locked. Bye."
}
trap cleanup EXIT INT TERM

sudo systemctl stop midibridge
.venv/bin/python3 bridge.py --setup
