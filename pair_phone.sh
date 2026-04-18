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
BT_HCI="${BT_HCI:-hci0}"
BLUEALSA_INITIAL_VOLUME="${BLUEALSA_INITIAL_VOLUME:-70}"
BLUEALSA_KEEP_ALIVE="${BLUEALSA_KEEP_ALIVE:--1}"
BLUEALSA_IO_RT_PRIORITY="${BLUEALSA_IO_RT_PRIORITY:-20}"
BLUEALSA_CMD="$(command -v bluealsad || command -v bluealsa || true)"
PKILL_CMD="$(command -v pkill || true)"
SYSTEMCTL_CMD="$(command -v systemctl || true)"
TEMP_BLUEALSA_PID=""

cleanup() {
  if [[ -n "$TEMP_BLUEALSA_PID" ]] && kill -0 "$TEMP_BLUEALSA_PID" 2>/dev/null; then
    kill "$TEMP_BLUEALSA_PID" 2>/dev/null || true
    wait "$TEMP_BLUEALSA_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

if [[ -n "$SYSTEMCTL_CMD" ]]; then
  "$SYSTEMCTL_CMD" stop bluealsa.service bluealsa-aplay.service bt-speaker-agent.service 2>/dev/null || true
fi

if [[ -n "$PKILL_CMD" ]]; then
  "$PKILL_CMD" -f '/usr/bin/bluealsa -S' 2>/dev/null || true
  "$PKILL_CMD" -f '/usr/bin/bluealsa-aplay -S' 2>/dev/null || true
fi

if [[ -n "$BLUEALSA_CMD" ]]; then
  "$BLUEALSA_CMD" \
    --initial-volume "$BLUEALSA_INITIAL_VOLUME" \
    --keep-alive "$BLUEALSA_KEEP_ALIVE" \
    --io-rt-priority "$BLUEALSA_IO_RT_PRIORITY" \
    --device "$BT_HCI" \
    --profile hfp-hf \
    --profile hsp-hs &
  TEMP_BLUEALSA_PID=$!
  sleep 1
  if ! kill -0 "$TEMP_BLUEALSA_PID" 2>/dev/null; then
    echo "Warning: temporary BlueALSA did not stay running, so the phone may not see call-audio profiles while pairing." >&2
  fi
else
  echo "Warning: bluealsa/bluealsad not found in PATH, so the phone may not see call-audio profiles while pairing." >&2
fi

bluetoothctl <<EOF
power on
agent NoInputNoOutput
default-agent
system-alias $BT_ALIAS
pairable on
discoverable on
discoverable-timeout 0
quit
EOF

echo
echo "Adapter is now pairable/discoverable as: $BT_ALIAS"
echo "Keep this terminal open while pairing so the phone can see the HFP/HSP call profiles."
echo "Pair to $BT_ALIAS on the phone, then press Enter here once the phone shows it is connected."
read -r _

echo
echo "Detected Bluetooth devices:"
bluetoothctl devices || true
echo
echo "Copy the phone MAC into bridge.conf as PHONE_MAC=XX:XX:XX:XX:XX:XX if it is not already set."
