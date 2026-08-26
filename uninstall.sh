#!/bin/bash
# Uninstaller for the scheduled_print Klipper extra.
#
# Usage:
#   ./uninstall.sh
#
# Environment overrides:
#   KLIPPER_PATH - path to the klipper repo (default: ~/klipper)

set -eu

KLIPPER_PATH="${KLIPPER_PATH:-${HOME}/klipper}"
EXTRAS_DIR="${KLIPPER_PATH}/klippy/extras"
TARGET="${EXTRAS_DIR}/scheduled_print.py"

if [ -L "${TARGET}" ]; then
    rm -f "${TARGET}"
    echo "Removed symlink at ${TARGET}."
else
    echo "No symlink found at ${TARGET} -- nothing to do."
fi

echo "Remove the [scheduled_print] section from printer.cfg, then RESTART Klipper."
