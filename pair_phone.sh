#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/bridge.conf}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_PATH"
BT_ALIAS="${BT_ALIAS:-PiCallBridge}"

bluetoothctl <<EOF
power on
agent on
default-agent
system-alias $BT_ALIAS
pairable on
pairable-timeout 0
discoverable on
discoverable-timeout 0
quit
EOF

echo
echo "Adapter is now pairable/discoverable as: $BT_ALIAS"
echo "Open Bluetooth settings on the phone and pair to that name."
echo "Then run: bluetoothctl devices"
echo "Copy the phone MAC into bridge.conf as PHONE_MAC=XX:XX:XX:XX:XX:XX"
