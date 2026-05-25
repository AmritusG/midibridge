# midibridge aliases for managing the Pi from your laptop.
#
# Usage:
#   1. Append to ~/.zshrc (or ~/.bashrc):
#        cat aliases.sh >> ~/.zshrc
#   2. Reload your shell:
#        source ~/.zshrc
#   3. Use the aliases below from any terminal.
#
# Assumes the Pi is reachable as "mididodi.local" via mDNS, the SSH
# user is "admin", and the bridge is installed at /home/admin/midibridge.
# If your setup differs, edit MDD_HOST / MDD_USER / MDD_PATH below.

MDD_HOST="mididodi.local"
MDD_USER="admin"
MDD_PATH="/home/admin/midibridge"


# ----- Shell access -------------------------------------------------------

# SSH into the Pi for ad-hoc work.
alias mdd='ssh -t ${MDD_USER}@${MDD_HOST}'


# ----- Service control ----------------------------------------------------

# Restart the bridge service and show the first 10 lines of fresh log
# output (handy for spotting config errors at startup).
alias mdd-restart='ssh -t ${MDD_USER}@${MDD_HOST} "sudo systemctl restart midibridge && sleep 1 && journalctl -u midibridge -n 10 --no-pager"'

# Service health summary.
alias mdd-status='ssh ${MDD_USER}@${MDD_HOST} "systemctl status midibridge --no-pager | head -15"'


# ----- Logs ---------------------------------------------------------------

# Follow the journal in real time. Ctrl+C to exit.
alias mdd-log='ssh ${MDD_USER}@${MDD_HOST} "journalctl -u midibridge -f --no-pager"'

# Show the most recent 30 journal lines, then exit.
alias mdd-tail='ssh ${MDD_USER}@${MDD_HOST} "journalctl -u midibridge -n 30 --no-pager"'


# ----- Configuration ------------------------------------------------------

# Edit config.yaml on the Pi in nano. Save with Ctrl+O, exit with Ctrl+X.
# Changes don't apply until the service is restarted -- use mdd-restart
# after saving.
alias mdd-cfg='ssh -t ${MDD_USER}@${MDD_HOST} nano ${MDD_PATH}/config.yaml'


# ----- Trim lock helpers --------------------------------------------------

# Unlock trim knobs temporarily for sound-check. Runs the bridge in
# setup mode interactively. Ctrl+C exits, automatically restarting the
# locked service.
alias mdd-trim='ssh -t ${MDD_USER}@${MDD_HOST} "${MDD_PATH}/unlock-trim.sh"'

# Permanently lock trim knobs (operation mode). Flips the config flag
# and restarts the service.
mdd-lock() {
    ssh -t ${MDD_USER}@${MDD_HOST} "sed -i 's/lock_trim_in_operation_mode: false/lock_trim_in_operation_mode: true/' ${MDD_PATH}/config.yaml && sudo systemctl restart midibridge && journalctl -u midibridge -n 6 --no-pager"
}

# Permanently unlock trim knobs. Flips the config flag and restarts.
mdd-unlock() {
    ssh -t ${MDD_USER}@${MDD_HOST} "sed -i 's/lock_trim_in_operation_mode: true/lock_trim_in_operation_mode: false/' ${MDD_PATH}/config.yaml && sudo systemctl restart midibridge && journalctl -u midibridge -n 6 --no-pager"
}
