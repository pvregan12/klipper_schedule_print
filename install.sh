#!/bin/bash
# Installer for the scheduled_print Klipper extra.
#
# Symlinks scheduled_print.py into klippy/extras/ so future
# `git pull`s in this repo are picked up by Klipper without
# re-copying anything.
#
# Usage:
#   ./install.sh
#
# Environment overrides:
#   KLIPPER_PATH    - path to the klipper repo (default: ~/klipper)
#   KLIPPER_SERVICE - systemd service name for klipper (default: klipper)

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
KLIPPER_SERVICE="${KLIPPER_SERVICE:-klipper}"
EXTRAS_DIR="${KLIPPER_PATH}/klippy/extras"

if [ ! -d "${EXTRAS_DIR}" ]; then
    echo "Could not find ${EXTRAS_DIR}."
    echo "Set KLIPPER_PATH to your klipper checkout and re-run, e.g.:"
    echo "  KLIPPER_PATH=/home/pi/klipper ./install.sh"
    exit 1
fi

echo "Linking scheduled_print.py into ${EXTRAS_DIR}"
ln -sf "${SCRIPT_DIR}/scheduled_print.py" "${EXTRAS_DIR}/scheduled_print.py"

echo
echo "Add this to printer.cfg if it isn't there already:"
echo
echo "[scheduled_print]"
echo "#home_gcode: G28"
echo "#clean_nozzle_gcode: CLEAN_NOZZLE"
echo "#print_start_gcode: PRINT_START"
echo

if command -v systemctl >/dev/null 2>&1; then
    read -r -p "Restart the ${KLIPPER_SERVICE} service now? [y/N] " REPLY
    case "$REPLY" in
        [yY]*)
            sudo systemctl restart "${KLIPPER_SERVICE}"
            echo "Restarted ${KLIPPER_SERVICE}."
            ;;
        *)
            echo "Skipped restart -- run RESTART in the console, or restart the service manually."
            ;;
    esac
else
    echo "systemctl not found -- restart Klipper manually (RESTART in the console)."
fi

echo "Done."
